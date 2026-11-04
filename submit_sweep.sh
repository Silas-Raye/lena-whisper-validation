#!/bin/bash
# submit_sweep.sh — transcribe the package's clips with each model and setting
# in a plan, one SLURM array task per run.
#
# Usage (on the ACCRE gateway):
#   bash ~/lena-whisper-validation/submit_sweep.sh                 round 1 (SWEEP_PLAN)
#   bash ~/lena-whisper-validation/submit_sweep.sh more            round 2 (SWEEP_PLAN_MORE)
#   bash ~/lena-whisper-validation/submit_sweep.sh turbo no_cond   any model and settings
#
# Runs that are done are skipped. Each task stops at the time limit and keeps
# every finished clip, so run this again after each round of jobs until
# status.sh shows every run done.

set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
cd "$LV_DIR"

if [ ! -f "${VENV_DIR}/bin/activate" ]; then
    echo "[X] Setup hasn't been run yet. Run: bash ${SHOW_DIR}/setup.sh"
    exit 1
fi
total=$(n_clips)
if [ "$total" -eq 0 ]; then
    echo "[X] The package has no clips yet. Run: bash ${SHOW_DIR}/make_package.sh"
    exit 1
fi

if [ "$#" -eq 0 ]; then
    mapfile -t runs < <(plan_runs "${SWEEP_PLAN[@]}")
    label="round 1 (SWEEP_PLAN)"
elif [ "$1" = "more" ]; then
    mapfile -t runs < <(plan_runs "${SWEEP_PLAN_MORE[@]}")
    label="round 2 (SWEEP_PLAN_MORE)"
else
    mapfile -t runs < <(plan_runs "$*")
    label="$*"
fi

mkdir -p logs runs
# Freeze the list for this submission, so editing config.sh while jobs wait
# in the queue can't change which run a task does.
snapshot="runs/sweep_$(date +%Y%m%d_%H%M%S).txt"
: > "$snapshot"
echo "[*] ${label}, ${total} clips:"
n_queued=0
for run in "${runs[@]}"; do
    read -r model config <<< "$run"
    done_n=$(n_done "$model" "$config")
    if [ "$done_n" -ge "$total" ]; then
        printf "    %-12s %-18s done\n" "$model" "$config"
    else
        n_queued=$((n_queued + 1))
        echo "$model $config" >> "$snapshot"
        printf "    %-12s %-18s %3s/%s clips, task %s\n" "$model" "$config" "$done_n" "$total" "$n_queued"
    fi
done
if [ "$n_queued" -eq 0 ]; then
    rm -f "$snapshot"
    echo "[✓] Every run is done. Score them: bash ${SHOW_DIR}/score.sh"
    exit 0
fi

# A second submission would run the same clips twice, so wait for the queue
# to empty instead.
n_existing=$( { squeue --me -h -n whisper_sweep -r 2>/dev/null || true; } | wc -l)
if [ "$n_existing" -gt 0 ]; then
    rm -f "$snapshot"
    echo
    echo "[X] You already have ${n_existing} sweep task(s) queued or running. Run this"
    echo "    again when they're done (bash ${SHOW_DIR}/status.sh), or cancel them"
    echo "    first: scancel --name=whisper_sweep"
    exit 1
fi

sbatch_args=(
    --account="$SLURM_ACCOUNT"
    --partition="$SLURM_PARTITION"
    --gres="$SLURM_GRES"
    --time="$SLURM_TIME"
    --cpus-per-task="$SLURM_CPUS"
    --mem="$SLURM_MEM"
    --array="1-${n_queued}%${MAX_PARALLEL}"
    --output="${LV_DIR}/logs/%x_%A_%a.log"
)
if [ -n "$SLURM_QOS" ]; then
    sbatch_args+=(--qos="$SLURM_QOS")
fi
if [ -n "${SLURM_EXCLUDE:-}" ]; then
    sbatch_args+=(--exclude="$SLURM_EXCLUDE")
fi

job_id=$(sbatch --parsable "${sbatch_args[@]}" run_sweep.sh "${LV_DIR}/${snapshot}" "$PACKAGE_DIR")
job_id="${job_id%%;*}"
echo
echo "[✓] Submitted job ${job_id} with ${n_queued} task(s)."
echo "    Progress: bash ${SHOW_DIR}/status.sh"
echo "    Logs:     ${SHOW_DIR}/logs/whisper_sweep_${job_id}_<task>.log"
if [[ "$SLURM_QOS" == debug* ]]; then
    echo
    echo "    Each task stops after ${SLURM_TIME} and keeps every finished clip."
    echo "    When the queue is empty, run this script again to continue."
fi
