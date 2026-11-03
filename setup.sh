#!/bin/bash
# setup.sh — one-time setup for the LENA Whisper validation tools on ACCRE.
#
# Usage (on the ACCRE gateway):
#   bash ~/lena-whisper-validation/setup.sh
#
# Safe to run again: each step skips work that's already done.

set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

step() { printf "\n[%s/5] %s\n" "$1" "$2"; }

# ---- 0. Make sure we're on ACCRE --------------------------------------------
if [ ! -d "$(dirname "$DATA_ROOT")" ] || ! command -v sbatch >/dev/null; then
    echo "[X] Run this on ACCRE, not on your own computer:"
    echo "      ssh <VUNetID>@login.accre.vu"
    echo "      bash ~/lena-whisper-validation/setup.sh"
    exit 1
fi

# ---- 1. SLURM account ---------------------------------------------------------
step 1 "Check access to the lab's GPU account"
found=$(sacctmgr -n -P show assoc where user="$USER" account="$SLURM_ACCOUNT" format=Account 2>/dev/null || true)
if [ -n "$found" ]; then
    echo "    [✓] You can submit jobs to ${SLURM_ACCOUNT}"
else
    echo "    [!] Your user isn't in ${SLURM_ACCOUNT}, so GPU jobs will fail with"
    echo "        \"Invalid account\". Ask the lab to have ACCRE add you. Setup continues."
fi

# ---- 2. Data folders ----------------------------------------------------------
step 2 "Create your folders in /nobackup"
mkdir -p "$DATA_ROOT"
# Recordings are research data: only you can read your folder.
chmod 700 "$DATA_ROOT"
mkdir -p "$CLIPS_IN" "$(dirname "$PACKAGE_DIR")" "$MODEL_DIR" "$ITS_DIR"
for dir in "$VAL_ROOT" "$CLIPS_IN" "$ITS_DIR" "$MODEL_DIR"; do
    echo "    [✓] ${dir}"
done
mkdir -p "${LV_DIR}/logs" "${LV_DIR}/runs"

# ---- 3. Python environment ----------------------------------------------------
step 3 "Set up the Python environment ($(show "$VENV_DIR"))"
set +eu   # ACCRE's environment scripts aren't written for strict mode
if ! type setup_accre_software_stack >/dev/null 2>&1; then
    source /etc/profile >/dev/null 2>&1
fi
if ! type setup_accre_software_stack >/dev/null 2>&1; then
    echo "[X] ACCRE's setup_accre_software_stack command isn't available."
    echo "    Log out, log back in, and run setup again."
    exit 1
fi
setup_accre_software_stack
set -eu

# pip unpacks into /nobackup: the gateway limits /tmp to 2 GB per user.
pip_tmp="${DATA_ROOT}/.pip-tmp"
if [ "$VENV_DIR" = "$MAIN_VENV" ]; then
    echo "    [✓] Using the main pipeline's environment"
elif [ ! -f "${VENV_DIR}/bin/activate" ]; then
    echo "    Creating the environment..."
    python3 -m venv "$VENV_DIR"
fi
set +u
# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"
set -u

if python -c "import whisper" 2>/dev/null; then
    echo "    [✓] Whisper is installed"
else
    echo "    Installing Whisper and PyTorch. This downloads several GB and can"
    echo "    take 10-20 minutes..."
    mkdir -p "$pip_tmp"
    TMPDIR="$pip_tmp" pip install --no-cache-dir --upgrade pip
    TMPDIR="$pip_tmp" pip install --no-cache-dir openai-whisper
    rm -rf "$pip_tmp"
    echo "    [✓] Whisper installed"
fi
# Silero VAD needs only PyTorch, which Whisper brought. --no-deps keeps pip
# from touching PyTorch (its optional extras would pull in torchaudio).
if python -c "import silero_vad" 2>/dev/null; then
    echo "    [✓] Silero VAD is installed"
elif pip install --no-cache-dir --no-deps silero-vad packaging >/dev/null \
        && python -c "import silero_vad" 2>/dev/null; then
    echo "    [✓] Silero VAD installed"
else
    echo "    [!] Silero VAD didn't install, so the vad_gate and lena_or_vad settings"
    echo "        will fail. Setup continues; run it again to retry."
fi

# ---- 4. Whisper models --------------------------------------------------------
step 4 "Download the Whisper models in config.sh's plans (once)"
# Jobs may not reach the internet, and a download would use their GPU time.
# large-v2 is usually in ~/.cache/whisper already, from the main pipeline;
# the others go in MODEL_DIR on /nobackup, to keep your home folder small.
models=$(plan_runs "${SWEEP_PLAN[@]}" "${SWEEP_PLAN_MORE[@]}" | awk '!seen[$1]++ {print $1}')
for model in $models; do
    python - "$model" "$MODEL_DIR" <<'EOF' || echo "    [!] ${model} didn't download; the first job that uses it will try again."
import os, sys, whisper
name, model_dir = sys.argv[1], sys.argv[2]
if name not in whisper._MODELS:
    sys.exit(f"    [!] openai-whisper has no model called {name}; fix config.sh")
fname = os.path.basename(whisper._MODELS[name])
cache = os.path.join(os.getenv("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "whisper")
if os.path.isfile(os.path.join(cache, fname)):
    print(f"    [✓] {name} is in {cache.replace(os.path.expanduser('~'), '~')}")
else:
    whisper._download(whisper._MODELS[name], model_dir, False)
    print(f"    [✓] {name} is in {model_dir}")
EOF
done
set +u
deactivate
set -u

# ---- 5. Your commands ---------------------------------------------------------
step 5 "Write your commands to ${SHOW_DIR}/my_commands.txt"
cat > "${LV_DIR}/my_commands.txt" <<EOF
My commands (${USER})
=====================================================================

Run each step on ACCRE unless it says otherwise. The README explains each.
Every step works on the package named in config.sh (PACKAGE_NAME, now
${PACKAGE_NAME}).


1. Copy the children's recordings and .its files to ACCRE with the main
   pipeline: put their IDs in ~/lena-whisper/participant_ids.txt, then

       bash ~/lena-whisper/print_wav_commands.sh
       bash ~/lena-whisper/print_its_commands.sh

   and run the printed commands on YOUR computer.


2. Choose the clips

       bash ${SHOW_DIR}/make_sample.sh <ID> [<ID> ...]


3. Build the package for the transcription team

       bash ${SHOW_DIR}/make_package.sh


4. Copy the package to the lab server (and, later, the transcripts back)

       bash ${SHOW_DIR}/print_package_commands.sh


5. Run Whisper's settings and models on the clips (repeat until done)

       bash ${SHOW_DIR}/submit_sweep.sh
       bash ${SHOW_DIR}/status.sh


6. Score the runs against the human transcripts

       bash ${SHOW_DIR}/score.sh
EOF
echo "    [✓] Saved. View it with: cat ${SHOW_DIR}/my_commands.txt"

cat <<EOF

[✓] Setup complete. The steps are in ${SHOW_DIR}/my_commands.txt.
EOF
