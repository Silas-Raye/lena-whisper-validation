#!/bin/bash
# print_input_commands.sh — print the commands that copy sample.csv, the clips,
# and the clips' .its files to ACCRE.
#
# Usage (on the ACCRE gateway):
#   bash ~/lena-whisper-validation/print_input_commands.sh
#
# Then paste the commands into a terminal on YOUR computer, connected to the
# VPN and the lab server. The .its commands need sample.csv on ACCRE, so run
# this again after copying it.

set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

host="${USER}@login.accre.vu"
win_its="${SERVER_WIN}\\${SERVER_ITS//\//\\}"

cat <<EOF

Copy the inputs to ACCRE
=====================================================================

Run these on YOUR computer, not on ACCRE. Each scp asks for your VUNetID
password. Replace the <...> parts with the folders on your computer.


1. sample.csv (tools/make_validation_sample.py writes it)

   scp "<folder>/sample.csv" ${host}:${VAL_ROOT}/


2. The clips, if they're already cut: the whole folder, in one go (for the
   2026 validation sample, the validation_chunks folder in Dylan's
   validationSample folder). Clips that aren't here are cut from full
   recordings in ${RAW_DIR}.

   scp -r "<folder>" ${host}:${CLIPS_IN}/

EOF

if [ ! -f "$SAMPLE_CSV" ]; then
    echo "3. The .its files: copy sample.csv first, then run this again."
    echo
    exit 0
fi

# The .its file names in sample.csv's its_file column (python3's csv module
# handles quoted fields, which plain shell tools don't).
mapfile -t names < <(python3 - "$SAMPLE_CSV" <<'EOF'
import csv, sys
with open(sys.argv[1], newline="", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        name = (row.get("its_file") or "").strip()
        if name:
            print(name[:-4] if name.lower().endswith(".its") else name)
EOF
)
mapfile -t names < <(printf '%s\n' "${names[@]+"${names[@]}"}" | awk 'NF && !seen[$0]++')
n_have=0
todo=()
for name in "${names[@]+"${names[@]}"}"; do
    # In ITS_DIR, or in a folder copied into it (its_upload below).
    if [ -f "${ITS_DIR}/${name}.its" ] || compgen -G "${ITS_DIR}/*/${name}.its" >/dev/null; then
        n_have=$((n_have + 1))
    else
        todo+=("$name")
    fi
done

echo "3. The .its files: ${#names[@]} named in sample.csv, ${n_have} already on ACCRE."
echo "   They give the templates LENA's hints and the LENA gates their regions."
echo
if [ "${#todo[@]}" -eq 0 ]; then
    echo "   Nothing to copy."
    echo
    exit 0
fi

# Collect the files in a folder on your computer first, then copy that folder
# with one scp (one password), instead of one scp per file.
cat <<EOF
   The commands gather the missing ones (${#todo[@]}) in an its_upload folder
   on your computer, then copy it to ACCRE with one scp.

   WINDOWS (Command Prompt; the lab server mapped to ${SERVER_WIN})
   -----------------------------------------------------------------

mkdir "%USERPROFILE%\\its_upload"
EOF
for ((i = 0; i < ${#todo[@]}; i += 10)); do
    files=""
    for name in "${todo[@]:i:10}"; do
        files+=" \"${win_its}\\${name}.its\""
    done
    echo "for /f \"delims=\" %i in ('dir /s /b${files} 2^>nul') do copy \"%i\" \"%USERPROFILE%\\its_upload\\\""
done
cat <<EOF
scp -r "%USERPROFILE%\\its_upload" ${host}:${ITS_DIR}/

   MAC (Terminal)
   -----------------------------------------------------------------

mkdir -p ~/its_upload
EOF
for ((i = 0; i < ${#todo[@]}; i += 10)); do
    match=""
    for name in "${todo[@]:i:10}"; do
        match+="${match:+ -o }-name \"${name}.its\""
    done
    echo "find \"${SERVER_MAC}/${SERVER_ITS}\" \\( ${match} \\) -exec cp {} ~/its_upload/ \\;"
done
echo "scp -r ~/its_upload ${host}:${ITS_DIR}/"
echo
