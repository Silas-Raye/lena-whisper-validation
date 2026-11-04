# shellcheck shell=bash disable=SC2034
# config.sh — lab-wide settings for the LENA Whisper validation tools.
#
# Every user shares these values, so nobody edits them to get started.
# Per-user paths come from $USER, which on ACCRE is your VUNetID.
# A lab maintainer changes this file when the lab's setup changes.

# ---- Where your files live on ACCRE -------------------------------------------
# /nobackup is large, fast, and NOT backed up. Originals stay on the lab server.
DATA_ROOT="${LV_DATA_ROOT:-/nobackup/userspace/${USER}}"
VAL_ROOT="${DATA_ROOT}/lena_validation"
# One package per batch of clips sent to the transcription team. Every script
# works on this one; to work on another, change the name here or start the
# command with LV_PACKAGE=<name>, as in: LV_PACKAGE=legacy_2026 bash status.sh
# The team sees the name, so keep it neutral (no ages or IDs).
PACKAGE_NAME="${LV_PACKAGE:-batch1}"
PACKAGE_DIR="${VAL_ROOT}/packages/${PACKAGE_NAME}"
# The package's list of clips: make_sample.sh writes it (README step 2).
SAMPLE_CSV="${PACKAGE_DIR}/sample.csv"
# Clips that are already cut, named <clip_id>.wav.
CLIPS_IN="${VAL_ROOT}/clips_in"
# Full recordings and .its files: the main pipeline's folders, so files you
# copy with its print_wav_commands.sh and print_its_commands.sh are used here.
# A clip missing from CLIPS_IN is cut from its recording in RAW_DIR.
RAW_DIR="${DATA_ROOT}/rawAudio"
ITS_DIR="${LV_ITS_DIR:-${DATA_ROOT}/itsFiles}"
# The main pipeline's .its lookup table, which names each recording's .its file.
LOOKUP_TABLE="${HOME}/lena-whisper/its_identifier_file_babies_crb.csv"
# The age whose recordings to use: the main pipeline's TIMEPOINT, so that
# print_wav_commands.sh copies the same recordings make_sample.sh looks for.
TIMEPOINT="$(sed -n 's/^TIMEPOINT="\(.*\)".*/\1/p' "${HOME}/lena-whisper/config.sh" 2>/dev/null | head -n 1)"
TIMEPOINT="${TIMEPOINT:-6-mo}"
# Whisper models other than the main pipeline's large-v2.
MODEL_DIR="${DATA_ROOT}/whisper-models"

# ---- Choosing clips from full recordings (make_sample.sh) ------------------------
# Per child: 5-minute blocks with adult speech, split over the child's low,
# medium, and high thirds of LENA's adult word count, and blocks with no adult
# words, to measure how much Whisper makes up when nobody talks. 12 per child
# is an hour of audio per child to transcribe.
SPEECH_CLIPS="10"
QUIET_CLIPS="2"

# ---- The templates ----------------------------------------------------------------
# Spans follow LENA's segments: one span per stretch where LENA heard speech,
# and one per stretch where it didn't, so every second gets transcribed.
SPAN_MAX_SEC="20"      # longer spans are split
MERGE_GAP_SEC="1.0"    # LENA speech closer together than this is one span
MIN_SPAN_SEC="2.0"     # shorter spans join the one before
WINDOW_SEC="15"        # span length for clips without an .its file
# Clips are renamed V001, V002, ... in shuffled order, so a name says nothing
# about whose recording it is. The seed keeps the order the same on reruns.
ID_PREFIX="V"
SHUFFLE_SEED="2026"
# Share of clips to give to two transcribers, to measure their agreement.
DOUBLE_FRACTION="0.15"

# ---- The lab server -------------------------------------------------------------
SERVER_WIN="X:"
SERVER_MAC="/Volumes/HumphreysLab"
SERVER_ITS="Daily_2/LENA/LENA_raw/BABIES_CRB/ITS_files/individual_exports/unzipped_files"
# Where the transcription team picks up packages and returns transcripts.
# CHECK this path before the first package goes out.
SERVER_SHARE="Daily_2/LENA/LENA_transcription_validation"

# ---- Software ----------------------------------------------------------------------
# If the main pipeline is set up (~/lena-whisper), its Python environment is
# reused, which saves about 5 GB in your home folder; setup.sh adds Silero VAD
# to it. Otherwise setup.sh makes an environment in this folder.
MAIN_VENV="${HOME}/lena-whisper/whisper-env"
if [ -n "${LV_VENV_DIR:-}" ]; then
    VENV_DIR="$LV_VENV_DIR"
elif [ -f "${MAIN_VENV}/bin/activate" ]; then
    VENV_DIR="$MAIN_VENV"
else
    VENV_DIR="${LV_DIR}/whisper-env"
fi
FFMPEG_MODULE="ffmpeg/7.1.1"

# ---- The comparison ---------------------------------------------------------------
# Each line is a model and the settings ("configs") to run it with. sweep.py
# describes every setting. "prod" is what the main pipeline runs today.
# submit_sweep.sh runs SWEEP_PLAN; "submit_sweep.sh more" runs SWEEP_PLAN_MORE.
# Round 1: the current model and two cheaper ones, each with the main settings.
SWEEP_PLAN=(
    "large-v2   prod no_cond no_cond_hst2 lena_or_vad"
    "turbo      prod no_cond no_cond_hst2 lena_or_vad"
    "small.en   prod no_cond no_cond_hst2 lena_or_vad"
)
# Round 2: the silence gate and other gates, greedy search, and medium.en.
# Trim it to what round 1 makes worth testing.
SWEEP_PLAN_MORE=(
    "large-v2   no_cond_nst4 no_cond_nst8 lena_gate vad_gate no_cond_greedy"
    "turbo      no_cond_nst4 no_cond_nst8 lena_gate"
    "small.en   no_cond_nst4 no_cond_nst8 lena_gate"
    "medium.en  no_cond no_cond_hst2 lena_or_vad"
)
# The run that score.sh compares the others with.
BASELINE_RUN="large-v2__prod"

# ---- GPU job settings --------------------------------------------------------------
# The same as the main pipeline's: the shared debug QoS gives 1 GPU per user
# and 30 minutes per job. Each job transcribes as many clips as it can;
# resubmitting continues where the last job stopped.
#
# When the lab gets a batch_gpu account, change these lines to:
#   SLURM_ACCOUNT="l3_humphreys_lab_acc"
#   SLURM_PARTITION="batch_gpu"
#   SLURM_QOS=""                        (leave empty)
#   SLURM_GRES="gpu:<type>:1"           (type from slurm_resources)
#   SLURM_TIME="08:00:00"
SLURM_ACCOUNT="l3_humphreys_lab_iacc"
SLURM_PARTITION="interactive_gpu"
SLURM_QOS="debug_iacc"
SLURM_GRES="gpu:nvidia_rtx_a4000:1"
SLURM_TIME="00:30:00"
SLURM_CPUS="2"
SLURM_MEM="16G"
MAX_PARALLEL="10"   # most array tasks running at once

# The ACCRE guest account's batch_gpu queue instead: RTX A6000s (48 GB), up
# to 6 at once, no 30-minute limit, so each run finishes in one job. Use it
# by starting submit_sweep.sh with LV_GPU=guest:
#   LV_GPU=guest bash submit_sweep.sh
if [ "${LV_GPU:-}" = "guest" ]; then
    SLURM_ACCOUNT="accre_guests_acc"
    SLURM_PARTITION="batch_gpu"
    SLURM_QOS=""
    SLURM_GRES="gpu:nvidia_rtx_a6000:1"
    SLURM_TIME="06:00:00"   # a large-v2 run of 60 clips takes about 1-2 hours
    MAX_PARALLEL="6"        # the account's limit
fi
# GPU nodes to keep jobs off, comma-separated. On 2026-11-04 every job sent to
# gpu0058 found its GPU already full (another user's process holding 15 of
# 16 GB). Empty this when ACCRE has fixed it.
SLURM_EXCLUDE="gpu0058"
