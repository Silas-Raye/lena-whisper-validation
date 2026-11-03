#!/bin/bash
# run_sweep.sh — one SLURM array task = one run (a model with one setting)
# over the package's clips.
#
# Don't sbatch this file yourself. submit_sweep.sh submits it and supplies the
# account, partition, GPU, time limit, and log path from config.sh.
#SBATCH --job-name=whisper_sweep
#SBATCH --nodes=1
#SBATCH --ntasks=1

LIST="${1:?This script is submitted by submit_sweep.sh; run that instead.}"

# The job starts in the folder it was submitted from: the repo folder.
# shellcheck source=lib.sh
source "${SLURM_SUBMIT_DIR}/lib.sh"
load_python

read -r MODEL CONFIG <<< "$(sed -n "${SLURM_ARRAY_TASK_ID}p" "$LIST")"
if [ -z "${CONFIG:-}" ]; then
    echo "[X] No run on line ${SLURM_ARRAY_TASK_ID} of ${LIST}"
    exit 1
fi

echo "=============================="
echo "Run:          ${MODEL}__${CONFIG}"
echo "Array task:   ${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
echo "Node:         ${SLURMD_NODENAME:-unknown}"
echo "Time limit:   ${SLURM_TIME}"
echo "Start time:   $(date)"
echo "=============================="

export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"
# -u writes output immediately, so the log keeps its last lines if SLURM
# stops the job at the time limit.
python -u "${LV_DIR}/sweep.py" --package "$PACKAGE_DIR" \
    --model "$MODEL" --config "$CONFIG" --model-dir "$MODEL_DIR"

echo "Finished: $(date)"
