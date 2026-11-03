#!/bin/bash
# make_package.sh — build the transcription package from sample.csv: the
# clips, a template for each, and the files only the analyst sees.
#
# Usage (on the ACCRE gateway):
#   bash ~/lena-whisper-validation/make_package.sh
#
# Takes a minute or two (it copies the clips and reads the .its files), so it
# runs here rather than as a job. Run it again after adding clips to
# sample.csv: existing clips keep their IDs and files.

set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

if [ ! -f "$SAMPLE_CSV" ]; then
    echo "[X] No sample.csv at ${SAMPLE_CSV}. See: bash ${SHOW_DIR}/print_input_commands.sh"
    exit 1
fi

load_python
# ffmpeg is only needed for audio that isn't 16 kHz mono 16-bit already.
set +eu
module load "$FFMPEG_MODULE" >/dev/null 2>&1
set -eu

python "${LV_DIR}/make_package.py" \
    --sample "$SAMPLE_CSV" --out "$PACKAGE_DIR" \
    --clips-dir "$CLIPS_IN" --raw-dir "$RAW_DIR" \
    --its-dir "$ITS_DIR" --lookup "$LOOKUP_TABLE" \
    --prefix "$ID_PREFIX" --seed "$SHUFFLE_SEED" \
    --span-max "$SPAN_MAX_SEC" --merge-gap "$MERGE_GAP_SEC" \
    --min-span "$MIN_SPAN_SEC" --window "$WINDOW_SEC" \
    --double "$DOUBLE_FRACTION"

echo
echo "    Next: give for_transcribers/ to the team (bash ${SHOW_DIR}/print_package_commands.sh)"
echo "    and start the Whisper runs (bash ${SHOW_DIR}/submit_sweep.sh)."
