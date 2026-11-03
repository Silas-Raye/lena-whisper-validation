#!/bin/bash
# status.sh — show the package, the Whisper runs' progress, the human
# transcripts that have come back, and any sweep jobs in the queue.
#
# Usage: bash ~/lena-whisper-validation/status.sh

set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

total=$(n_clips)
if [ "$total" -eq 0 ]; then
    echo "[X] No package yet at ${PACKAGE_DIR}. Run: bash ${SHOW_DIR}/make_package.sh"
    exit 1
fi
analyst="${PACKAGE_DIR}/analyst_only"
n_human=$(count_files "${analyst}/human/"*.csv "${analyst}/human/"*.xlsx)
n_human_clips=$( { ls "${analyst}/human/" 2>/dev/null || true; } \
    | { grep -Eio '^[a-z]+[0-9]+' || true; } | sort -u | wc -l)
n_double=0
if [ -f "${analyst}/double_transcribe.csv" ]; then
    n_double=$(( $(wc -l < "${analyst}/double_transcribe.csv") - 1 ))
fi

echo "Package ${PACKAGE_NAME}: ${total} clips, ${n_double} of them for two transcribers"
echo "Human transcripts back: ${n_human} files covering ${n_human_clips} clips"
echo

printf "%-12s %-18s %s\n" "MODEL" "SETTING" "CLIPS TRANSCRIBED"
# The plans' runs, then any other runs that have transcripts.
mapfile -t runs < <( { plan_runs "${SWEEP_PLAN[@]}" "${SWEEP_PLAN_MORE[@]}"
    for d in "${analyst}/whisper/"*__*; do
        if [ -d "$d" ]; then basename "$d" | sed 's/__/ /'; fi
    done; } | awk '!seen[$0]++')
for run in "${runs[@]}"; do
    read -r model config <<< "$run"
    printf "%-12s %-18s %s / %s\n" "$model" "$config" "$(n_done "$model" "$config")" "$total"
done

echo
jobs=$(squeue --me -h -r -n whisper_sweep -o "%.18i %.8T %.10M %.10L %R" 2>/dev/null || true)
if [ -n "$jobs" ]; then
    echo "Sweep tasks in the queue:"
    echo "$jobs"
else
    echo "No sweep tasks in the queue. To continue unfinished runs: bash ${SHOW_DIR}/submit_sweep.sh"
fi
