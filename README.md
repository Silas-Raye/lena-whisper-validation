# LENA Whisper validation on ACCRE

These scripts measure how accurate Whisper's transcripts of LENA recordings
are, and find the Whisper model and settings that make them most accurate.
For a sample of 5-minute clips they:

1. Build a package for the transcription team: the clips, and a template
   for each one to fill in from scratch. There's no Whisper transcript in
   it, so it can't bias the transcribers.
2. Keep apart what the team mustn't see: which recording each clip comes
   from, LENA's data, and Whisper's transcripts.
3. Transcribe the clips on ACCRE's GPUs with several models and settings:
   what the main pipeline runs today (`large-v2`), other settings, and
   cheaper models (`turbo`, `small.en`, `medium.en`).
4. Score every run against the human transcripts: word error rate (WER),
   speed, and how much speech Whisper makes up or misses.

It works alongside the main pipeline,
[lena-whisper](https://github.com/Silas-Raye/lena-whisper): it reads
recordings and .its files from the same folders and, if the main pipeline
is set up, uses its Python environment.

## Before you start

You need what the main pipeline needs: an ACCRE account in the lab's group
(`l3_humphreys_lab_iacc` for GPU jobs), access to the lab server through the
GlobalProtect VPN, and a terminal. You also need the clips, either already
cut (for the 2026 validation sample, the `validation_chunks` folder in
Dylan's `validationSample` folder) or as full recordings in your `rawAudio`
folder on ACCRE.

## Set up (once)

1. Log in to ACCRE, replacing `<VUNetID>` with yours:

   ```bash
   ssh <VUNetID>@login.accre.vu
   ```

2. Copy this repository into your home folder:

   ```bash
   git clone <REPO_URL> ~/lena-whisper-validation
   ```

3. Run setup:

   ```bash
   bash ~/lena-whisper-validation/setup.sh
   ```

Setup checks your GPU account, creates your folders in
`/nobackup/userspace/<VUNetID>/lena_validation/`, adds Silero VAD to the
Python environment (it uses the main pipeline's if you have it, which saves
about 5 GB; otherwise it installs Whisper here), downloads the models in the
plans in `config.sh`, and writes `~/lena-whisper-validation/my_commands.txt`.
You can run it again at any time.

## Run it

1. **On your computer, write `sample.csv`**, the list of clips. For the 2026
   validation sample:

   ```bash
   python tools/make_validation_sample.py "<path to>/validationSample" sample.csv
   ```

   It needs only Python 3.9 or newer. For another sample, write `sample.csv`
   yourself: one row per clip, with `clip_id`, `recording_id` (for example
   `1017_029796`), `start_sec` (where the clip starts in the recording),
   and optionally `duration_sec` (300 if empty) and `its_file`. Other
   columns, such as age or AWC level, are carried along, and the report
   breaks WER down by them.

2. **Copy `sample.csv`, the clips, and their .its files to ACCRE.** On
   ACCRE, run:

   ```bash
   bash ~/lena-whisper-validation/print_input_commands.sh
   ```

   It prints the copy commands; run them on your computer. Run it again
   after `sample.csv` is on ACCRE to get the commands for the .its files.

3. **Build the package:**

   ```bash
   bash ~/lena-whisper-validation/make_package.sh
   ```

4. **Give the package to the transcription team.** This prints the commands
   that copy the team's part to the lab server, and later bring the
   finished templates back:

   ```bash
   bash ~/lena-whisper-validation/print_package_commands.sh
   ```

   Check `SERVER_SHARE` in `config.sh` first: it's where the team picks up
   the package. `analyst_only/double_transcribe.csv` lists the clips to give
   to two transcribers each (15%); assign the rest to one transcriber each.

5. **Run Whisper on the clips**, while the team transcribes:

   ```bash
   bash ~/lena-whisper-validation/submit_sweep.sh        # round 1
   bash ~/lena-whisper-validation/status.sh
   ```

   Like the main pipeline, each job stops after 30 minutes and keeps every
   finished clip, so run `submit_sweep.sh` again after each round of jobs
   until `status.sh` shows every run done. Then run round 2 the same way
   with `submit_sweep.sh more`, or any model and settings with
   `submit_sweep.sh <model> <setting> [<setting> ...]`.

6. **Score the runs** once the finished templates are in
   `analyst_only/human/` (step 4's commands copy them there):

   ```bash
   bash ~/lena-whisper-validation/score.sh
   ```

   The report is in `analyst_only/reports/<date_time>/report.md`.

### How long the runs take

From the main pipeline's speed, a `large-v2` run on 96 clips takes about 2
GPU-hours, or four to five 30-minute jobs. `turbo` and `small.en` take a
fraction of that, and the report gives each run's measured speed. Round 1 is
12 runs. If the lab gets a `batch_gpu` account, change the GPU settings in
`config.sh` as it describes, and each run finishes in one job.

## What you get

Everything for a package is in
`/nobackup/userspace/<VUNetID>/lena_validation/packages/<PACKAGE_NAME>/`:

| Folder | Contents |
|---|---|
| `for_transcribers/` | **For the team.** `INSTRUCTIONS.txt`, `audio/V001.wav` ..., `templates/V001.csv` ... |
| `analyst_only/` | **Never share.** Everything below. |
| `analyst_only/key.csv` | Which recording and start time each clip is, with `sample.csv`'s other columns. |
| `analyst_only/double_transcribe.csv` | The clips to give to two transcribers. |
| `analyst_only/whisper/<model>__<setting>/` | Each run's transcripts: `V001.json` (Whisper's full output, with timing) and `V001.csv` (laid out on the template's rows, to read next to the human transcript). |
| `analyst_only/lena/`, `analyst_only/spans/` | LENA's segments in each clip, and each template row's LENA numbers. |
| `analyst_only/human/` | The finished templates, named `V001_<initials>.csv`. |
| `analyst_only/reports/` | `score.sh`'s reports. |

Clips are renamed `V001`, `V002`, ... in shuffled order, so a clip's name
says nothing about whose recording it is or the child's age.

### The template

One row per stretch of the clip ("span"), covering the whole clip with no
gaps:

| Column | Filled in by | Meaning |
|---|---|---|
| `clip`, `span` | the script | The clip and the row's number. |
| `start_sec`, `end_sec`, `start`, `end` | the script | Where the span is, in seconds and as minutes:seconds. |
| `lena_hint` | the script | What LENA thinks is in the span: `LENA heard: woman 3.2s`, or `LENA: no speech`. |
| `status` | the transcriber | `speech`, `none`, or `unclear` (talking, but no word can be made out). Every row needs one. |
| `speech` | the transcriber | Words said by people in the room, adults and children. |
| `media` | the transcriber | Words from a TV, radio, phone, video, or talking toy. |
| `notes` | the transcriber | Anything else. |

`INSTRUCTIONS.txt` gives the transcription conventions: `xxx` for a word
that can't be made out, `[babble]` and other bracketed notes for sounds that
aren't words, and so on.

## How LENA is used

LENA's speech detection is a clue, never the final word:

- **Templates.** Span edges follow LENA's segments, so a span usually starts
  and ends at a pause, and the hint says what LENA heard. But the spans
  cover every second, and every span needs a status, so the transcribers
  listen to the stretches LENA called silent too.
- **Where Whisper transcribes.** Whisper makes up words in long stretches
  without speech, so some settings transcribe only where speech was
  detected. `lena_gate` uses LENA's segments alone; `lena_or_vad`
  transcribes wherever LENA *or* the Silero voice detector hears speech;
  `vad_gate` uses Silero alone. The report shows what each one gains and
  loses.
- **Cleaning up afterwards.** The `lenacheck` post-filter drops a Whisper
  segment only when LENA heard no speech near it *and* Whisper itself was
  unsure of it.
- **Checking LENA.** The report says what share of the speech the
  transcribers heard LENA flagged, and how many reference words are in
  stretches LENA called silent: the speech a LENA-only gate would lose.

## The settings

Each setting changes a few things from `prod`, which is what the main
pipeline runs today (beam search with 5 beams, everything else
openai-whisper's default). `sweep.py` lists them all; any model works with
any setting.

| Setting | What it changes | What it's for |
|---|---|---|
| `prod` | nothing | The baseline. |
| `no_cond` | Doesn't prompt each 30-second window with the last window's text. | Repetition loops, such as "Hang on." for four minutes. |
| `no_cond_hst2` | `no_cond`, plus skips silent stretches of 2+ seconds in a segment that looks made up. | Words made up in silence. |
| `no_cond_nst4`, `no_cond_nst8` | `no_cond`, with the silence threshold at 0.4 (skip more) or 0.8 (skip less). | Trading made-up words against missed ones. |
| `no_cond_lp08`, `no_cond_cr20` | Stricter confidence and repetition checks. | The same trade, by other means. |
| `no_cond_greedy` | No beam search. | About twice as fast. |
| `no_cond_prompt` | A prompt in the household register. | Spelling and style; can also leak into transcripts. |
| `lena_gate`, `vad_gate`, `lena_or_vad`, `lena_or_vad_hst2` | Transcribes only where LENA, Silero, or either one hears speech. | See [How LENA is used](#how-lena-is-used). |

The plans in `config.sh` (`SWEEP_PLAN`, `SWEEP_PLAN_MORE`) say which models
run with which settings. Round 1 runs the four main settings with
`large-v2`, `turbo` (large-v3-turbo: a much smaller decoder, several times
faster), and `small.en` (a sixth the size of large-v2). Round 2 tries the
silence threshold, the gates, and `medium.en`. Edit round 2 to follow up on
what round 1 shows.

## Reading the report

- **Tuning WER and held-out WER.** The clips are split once into tuning
  clips (60%) and held-out clips (40%). Pick settings by tuning WER, and
  report the held-out WER of the one you pick: picking and reporting on the
  same clips makes the winner look better than it is.
- **vs baseline [95% CI].** The difference from `large-v2__prod`, with an
  interval from resampling clips. An interval that includes 0 isn't a clear
  difference.
- **Best settings for each model.** Answers whether a cheaper model, tuned,
  matches `large-v2`: compare WER and speed.
- **made-up/min** is Whisper's words per minute of spans the transcriber
  marked `none`; **missed %** is the share of reference words in spans
  where Whisper wrote nothing; **live-only** is WER on spans without TV,
  radio, or toy speech.
- **Transcribers vs. each other.** The WER of one transcriber against
  another on the double-transcribed clips. Differences between runs smaller
  than this are hard to call real.
- **Post-filters.** Every run is also scored after `norepeat` (drop the 3rd
  and later copies of a repeated segment), `lenacheck`, and both. They need
  no GPU time, and the main pipeline could apply the winner to its
  transcripts afterwards.

## Provisional scores from the 2026 corrections

Before the new transcripts come back, you can score the runs against the
corrections the team made in 2026. They were made by editing Whisper's
output, so they favor runs that resemble it; use them for a first look only.

1. On your computer, with `pip install pandas openpyxl`:

   ```bash
   python tools/make_legacy_reference.py "<path to>/validationSample" legacy_reference.csv
   ```

2. Copy `legacy_reference.csv` to `/nobackup/userspace/<VUNetID>/lena_validation/`
   on ACCRE, and run:

   ```bash
   bash ~/lena-whisper-validation/score.sh --legacy /nobackup/userspace/<VUNetID>/lena_validation/legacy_reference.csv
   ```

The report also scores "original": the transcripts the team corrected.

## Troubleshooting

| What you see | Fix |
|---|---|
| `Invalid account or account/partition combination` | Ask the lab to have ACCRE add you to `l3_humphreys_lab_iacc`. |
| `make_package.sh` says a clip has no audio | Copy its `.wav` to `clips_in`, named `<clip_id>.wav`, or its full recording to `rawAudio`. |
| `no LENA data` for a clip | Its .its file isn't in `itsFiles` (`print_input_commands.sh` prints the copy commands). The clip gets 15-second spans with no hints; the LENA gates transcribe all of it. Delete its template and rerun `make_package.sh` after copying the .its file. |
| A log ends with `CANCELLED ... DUE TO TIME LIMIT` | Expected. Run `submit_sweep.sh` again. |
| `score.sh` skips a file: `has N rows, but the template has M` | The transcriber added or deleted rows. Fix the file, keeping one row per span. |
| `score.sh` compares fewer runs than you expect | Runs under 90% done are listed at the end of the report as not compared yet. |
| `Batch script contains DOS line breaks` | On ACCRE, run `sed -i 's/\r$//' ~/lena-whisper-validation/*.sh`. |

## For maintainers

| File | Purpose |
|---|---|
| `config.sh`, `lib.sh` | Settings, including the sweep plans, and shared shell helpers. |
| `setup.sh` | One-time setup for each user. |
| `print_input_commands.sh`, `print_package_commands.sh` | Print the copy commands. |
| `make_package.sh`, `make_package.py` | Build the package. |
| `submit_sweep.sh`, `run_sweep.sh`, `sweep.py` | Transcribe the clips with each run, on GPUs. |
| `status.sh` | Show progress. |
| `score.sh`, `score.py` | Score the runs and write the report. |
| `common.py` | Spans, file formats, text normalization, and WER. |
| `its.py` | Reads .its files; copied from the main pipeline's `combineTranscripts.py`, so fix bugs in both. |
| `INSTRUCTIONS_FOR_TRANSCRIBERS.txt` | Copied into each package. |
| `tools/` | Run on your own computer: `sample.csv` for the 2026 sample, and the 2026 corrections as a reference. |

Text is normalized for scoring with Whisper's English normalizer, the
standard one for reporting Whisper's WER. It ignores capitals, punctuation,
fillers (um, uh), and anything in brackets, and standardizes numbers,
spelling, and contractions.

No research data goes in the repository: `.gitignore` keeps out audio,
.its files, spreadsheets, `sample.csv`, and packages.
