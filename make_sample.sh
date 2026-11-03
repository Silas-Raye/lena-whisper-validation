#!/bin/bash
# make_sample.sh — choose 5-minute clips from children's full recordings for
# the package, and list them in its sample.csv.
#
# Usage (on the ACCRE gateway):
#   bash ~/lena-whisper-validation/make_sample.sh 1253 1254 1256 1269 1270
#
# First copy the children's recordings and .its files to ACCRE with the main
# pipeline: put the IDs in ~/lena-whisper/participant_ids.txt and run its
# print_wav_commands.sh and print_its_commands.sh. It uses the recordings at
# the main pipeline's TIMEPOINT (in ~/lena-whisper/config.sh).
#
# For each child it chooses SPEECH_CLIPS blocks with adult speech and
# QUIET_CLIPS without (config.sh); make_sample.py explains how. Children
# already in sample.csv keep their clips, so you can run it again to add more.
# Then build the package with make_package.sh.

set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

if [ "$#" -eq 0 ]; then
    echo "Usage: bash ${SHOW_DIR}/make_sample.sh <ID> [<ID> ...]   (for example 1253 1254)"
    exit 1
fi

load_python
echo "[*] Package ${PACKAGE_NAME}, ${TIMEPOINT} recordings in $(show "$RAW_DIR")"
python "${LV_DIR}/make_sample.py" "$@" \
    --out "$SAMPLE_CSV" --raw-dir "$RAW_DIR" --its-dir "$ITS_DIR" \
    --lookup "$LOOKUP_TABLE" --timepoint "$TIMEPOINT" \
    --speech "$SPEECH_CLIPS" --quiet "$QUIET_CLIPS" --seed "$SHUFFLE_SEED"

echo
echo "    Next: build the package (bash ${SHOW_DIR}/make_package.sh)."
