#!/bin/bash
# score.sh — score every Whisper run in the package against the human
# transcripts in analyst_only/human/.
#
# Usage (on the ACCRE gateway):
#   bash ~/lena-whisper-validation/score.sh
#   bash ~/lena-whisper-validation/score.sh --legacy <legacy_reference.csv>
#
# --legacy scores against the corrections made in 2026 instead
# (tools/make_legacy_reference.py), for a provisional look before the new
# transcripts come back. Other options go to score.py (python score.py --help).
# Takes a few minutes, so it runs here rather than as a job.

set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

if [ "$(n_clips)" -eq 0 ]; then
    echo "[X] No package yet. Run: bash ${SHOW_DIR}/make_package.sh"
    exit 1
fi
load_python
python "${LV_DIR}/score.py" --package "$PACKAGE_DIR" --baseline "$BASELINE_RUN" "$@"
