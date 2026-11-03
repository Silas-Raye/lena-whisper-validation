#!/bin/bash
# print_package_commands.sh — print the commands that copy the transcribers'
# part of the package to the lab server, bring the filled-in templates back
# to ACCRE, and copy the score reports to your computer.
#
# Usage (on the ACCRE gateway):
#   bash ~/lena-whisper-validation/print_package_commands.sh
#
# Then paste the commands into a terminal on YOUR computer, connected to the
# VPN and the lab server. Only for_transcribers/ goes to the lab server's
# shared folder (SERVER_SHARE in config.sh); analyst_only/ names the
# recordings and has Whisper's transcripts, so it stays out of the team's reach.

set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

if [ "$(n_clips)" -eq 0 ]; then
    echo "[X] No package yet. Run: bash ${SHOW_DIR}/make_package.sh"
    exit 1
fi

host="${USER}@login.accre.vu"
win_share="${SERVER_WIN}/${SERVER_SHARE}/${PACKAGE_NAME}"
win_share_mkdir="${win_share//\//\\}"   # cmd's mkdir needs backslashes
mac_share="${SERVER_MAC}/${SERVER_SHARE}/${PACKAGE_NAME}"
src="${PACKAGE_DIR}/for_transcribers"
human="${PACKAGE_DIR}/analyst_only/human"
reports="${PACKAGE_DIR}/analyst_only/reports"

cat <<EOF

Package ${PACKAGE_NAME}
=====================================================================

Run these on YOUR computer, not on ACCRE. Connect to the VPN and the lab
server first. Each scp asks for your VUNetID password. On the lab server,
${SERVER_SHARE}/${PACKAGE_NAME}/
gets the team's folder and an empty returned/ folder for finished transcripts.


WINDOWS (Command Prompt; the lab server mapped to ${SERVER_WIN})
---------------------------------------------------------------------

1. Give the package to the team:

mkdir "${win_share_mkdir}\\returned" 2>nul
scp -r "${host}:${src}" "${win_share}/"

2. When transcripts come back (V001_AB.csv, ... in returned\\):

scp "${win_share}/returned/*.csv" "${host}:${human}/"

3. Copy the score reports to your computer:

scp -r "${host}:${reports}" "<folder on your computer>"


MAC (Terminal)
---------------------------------------------------------------------

1. Give the package to the team:

mkdir -p "${mac_share}/returned"
scp -r "${host}:${src}" "${mac_share}/"

2. When transcripts come back (V001_AB.csv, ... in returned/):

scp "${mac_share}"/returned/*.csv "${host}:${human}/"

3. Copy the score reports to your computer:

scp -r "${host}:${reports}" "<folder on your computer>"

EOF
