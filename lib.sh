# shellcheck shell=bash disable=SC2034
# lib.sh — shared helpers. Other scripts source this file; don't run it directly.

LV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Your VUNetID on ACCRE; the paths and copy commands use it.
: "${USER:=$(id -un 2>/dev/null || whoami)}"
# shellcheck source=config.sh
source "${LV_DIR}/config.sh"
SHOW_DIR="${LV_DIR/#"$HOME"/"~"}"   # shorter path for messages

# A path with your home folder shown as ~.
show() { echo "${1/#"$HOME"/"~"}"; }

# Count the arguments that are existing files. An unmatched glob stays a
# literal pattern, which doesn't exist, so it counts as 0.
count_files() {
    local n=0 f
    for f in "$@"; do
        [ -e "$f" ] && n=$((n + 1))
    done
    echo "$n"
}

# Load ACCRE's software and activate the Python environment. ACCRE's
# environment scripts aren't written for strict mode, so it's off meanwhile.
load_python() {
    set +eu
    if ! type setup_accre_software_stack >/dev/null 2>&1; then
        source /etc/profile >/dev/null 2>&1
    fi
    if type setup_accre_software_stack >/dev/null 2>&1; then
        setup_accre_software_stack >/dev/null 2>&1
    fi
    if [ ! -f "${VENV_DIR}/bin/activate" ]; then
        echo "[X] No Python environment at $(show "$VENV_DIR"). Run: bash ${SHOW_DIR}/setup.sh"
        exit 1
    fi
    # shellcheck source=/dev/null
    source "${VENV_DIR}/bin/activate"
    set -eu
}

# The package's clips, and the clips a run has transcribed.
n_clips() { count_files "${PACKAGE_DIR}/for_transcribers/audio/"*.wav; }
n_done()  { count_files "${PACKAGE_DIR}/analyst_only/whisper/${1}__${2}/"*.json; }

# Print "<model> <config>" for each run in plan lines like
# "large-v2  prod no_cond", one run per line.
plan_runs() {
    local line model configs config
    for line in "$@"; do
        read -r model configs <<< "$line"
        for config in $configs; do
            echo "$model $config"
        done
    done
}
