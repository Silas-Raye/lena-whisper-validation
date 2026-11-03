#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_package.py — build a transcription package: the clips, a template for
each one, and the files that only the analyst sees.

make_package.sh runs this with the paths in config.sh. By hand:
    python make_package.py --sample sample.csv --out <package folder> \\
        [--clips-dir DIR] [--raw-dir DIR] [--its-dir DIR] [--lookup CSV]

sample.csv has one row per clip:
    clip_id       a name for the clip. If --clips-dir has <clip_id>.wav, that
                  file is the clip.
    recording_id  the recording the clip comes from, such as 1017_029796.
                  Needed to cut the clip from the full recording in
                  --raw-dir, and to find the recording's .its file.
    start_sec     where the clip starts in the recording, in seconds.
    duration_sec  optional; 300 when empty.
    its_file      optional; the .its file's name, such as
                  20230830_104607_029796. Without it, the .its file is found
                  as the main pipeline finds it: <recording_id>.its, or the
                  lookup table.
Other columns (age, AWC level, and so on) are copied to analyst_only/key.csv,
and score.py breaks WER down by them.

What it makes:
    for_transcribers/        give this folder to the transcription team
        INSTRUCTIONS.txt
        audio/V001.wav ...   the clips, renamed so names don't reveal whose
                             recording it is, in shuffled order
        templates/V001.csv   one row per span, covering the whole clip
    analyst_only/            never share this folder with transcribers
        key.csv              which recording each clip is, with the sample's columns
        double_transcribe.csv   clips to give to two transcribers each
        spans/V001.csv       the template's spans, with LENA's numbers
        lena/V001.csv        LENA's segments in the clip
        whisper/             sweep.py's transcripts, one folder per run
        human/               the filled-in templates, when they come back
        reports/             score.py's reports

Running it again adds the clips that are new in sample.csv. It never changes
a clip's ID or overwrites a clip or template that exists.
"""

import argparse
import glob
import os
import random
import shutil
import subprocess
import sys

import common as C
from its import CombineError, Its, find_its

# Windows consoles can't print every character (such as the check marks in
# messages); print a substitute rather than stop.
try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass


HERE = os.path.dirname(os.path.abspath(__file__))
KEY_COLUMNS = ["clip", "clip_id", "recording_id", "start_sec", "duration_sec", "its_file",
               "lena", "double", "audio_source"]
PACKAGE_README = """\
This folder is one transcription package.

  for_transcribers/   Give this folder to the transcription team.
  analyst_only/       Never share this folder with transcribers. It names the
                      recording behind each clip and holds Whisper's
                      transcripts, which would bias the transcribers.

Put the filled-in templates in analyst_only/human/, named <clip>_<initials>.csv
(for example V001_AR.csv), then run score.sh.
"""


def say(msg):
    print(msg, flush=True)


def ffmpeg_convert(src, dst, start=None, duration=None):
    """Convert (and optionally cut) any audio file to 16 kHz mono 16-bit .wav."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("it isn't 16 kHz mono 16-bit, and ffmpeg isn't available to "
                           "convert it (on ACCRE, make_package.sh loads ffmpeg)")
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    tmp = dst + ".partial.wav"
    cmd += ["-i", src, "-ac", "1", "-ar", str(C.RATE), "-acodec", "pcm_s16le", tmp]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + result.stderr.strip()[-300:])
    os.replace(tmp, dst)


def find_audio(row, clips_dir, raw_dir):
    """('clip', path) for a ready-cut clip, ('raw', path) for the full
    recording to cut it from, or (None, places looked)."""
    looked = []
    if clips_dir:
        # Directly in clips_dir, or in a folder copied into it with scp -r.
        path = os.path.join(clips_dir, row["clip_id"] + ".wav")
        found = [path] if os.path.isfile(path) else \
            sorted(glob.glob(os.path.join(clips_dir, "*", row["clip_id"] + ".wav")))
        if found:
            return "clip", found[0]
        looked.append(path)
    rec = row.get("recording_id", "")
    if raw_dir and rec:
        for path in (os.path.join(raw_dir, rec[:4], rec + ".wav"), os.path.join(raw_dir, rec + ".wav")):
            if os.path.isfile(path):
                return "raw", path
            looked.append(path)
    return None, looked


def write_audio(kind, src, dst, start, duration):
    """Write the clip to dst as 16 kHz mono 16-bit .wav; return its length."""
    if kind == "clip":
        if C.is_whisper_ready(src):
            shutil.copyfile(src, dst)
        else:
            ffmpeg_convert(src, dst)
    else:
        if start is None:
            raise RuntimeError("cutting it from the recording needs start_sec")
        if C.is_whisper_ready(src):
            C.cut_wav(src, dst, start, duration)
        else:
            ffmpeg_convert(src, dst, start, duration)
    return C.wav_info(dst)[3]


def find_lena(row, its_dir, lookup, cache):
    """(Its object or None, note) for the clip's recording."""
    rec = row.get("recording_id", "")
    if not its_dir or not rec:
        return None, "no recording_id" if its_dir else "no .its folder"
    path = None
    name = row.get("its_file", "")
    if name:
        name = name[:-4] if name.lower().endswith(".its") else name
        # In its_dir, or in a folder copied into it with scp -r.
        found = [p for p in [os.path.join(its_dir, name + ".its")] if os.path.isfile(p)] \
            or sorted(glob.glob(os.path.join(its_dir, "*", name + ".its")))
        path = found[0] if found else None
    if path is None:
        wanted = f"{name}.its or {rec}.its" if name else f"{rec}.its"
        if not lookup or not os.path.isfile(lookup):
            on_disk = os.path.join(its_dir, rec + ".its")
            if not os.path.isfile(on_disk):
                return None, f"no {wanted} in {its_dir}"
            path = on_disk
        else:
            try:
                path = find_its(rec, its_dir, lookup, row.get("timepoint", ""))
            except (CombineError, OSError) as e:
                return None, f"no {wanted} in {its_dir}, and the lookup table says: {e}"
    if path not in cache:
        try:
            cache[path] = Its(path)
        except (CombineError, ValueError, KeyError) as e:
            return None, f"can't read {os.path.basename(path)}: {e}"
    return cache[path], os.path.basename(path)


def main():
    ap = argparse.ArgumentParser(description="Build a transcription package.")
    ap.add_argument("--sample", required=True, help="sample.csv: the clips")
    ap.add_argument("--out", required=True, help="the package folder")
    ap.add_argument("--clips-dir", default="", help="folder of ready-cut clips, <clip_id>.wav")
    ap.add_argument("--raw-dir", default="", help="folder of full recordings, <recording_id>.wav")
    ap.add_argument("--its-dir", default="", help="folder of .its files")
    ap.add_argument("--lookup", default="", help="the main pipeline's .its lookup table")
    ap.add_argument("--prefix", default="V", help="clip IDs are this plus a number: V001")
    ap.add_argument("--seed", type=int, default=2026, help="seed for shuffling the IDs")
    ap.add_argument("--span-max", type=float, default=20.0, help="longest span, seconds")
    ap.add_argument("--merge-gap", type=float, default=1.0,
                    help="LENA speech closer than this is one span, seconds")
    ap.add_argument("--min-span", type=float, default=2.0, help="shortest span, seconds")
    ap.add_argument("--window", type=float, default=15.0,
                    help="span length for clips without LENA data, seconds")
    ap.add_argument("--double", type=float, default=0.15,
                    help="share of new clips to give to two transcribers")
    ap.add_argument("--instructions", default=os.path.join(HERE, "INSTRUCTIONS_FOR_TRANSCRIBERS.txt"))
    a = ap.parse_args()

    sample = C.read_csv(a.sample)
    if not sample or "clip_id" not in sample[0]:
        sys.exit(f"[X] {a.sample} needs a clip_id column (and one row per clip).")
    ids = [r["clip_id"] for r in sample]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        sys.exit(f"[X] These clip_ids are in {a.sample} more than once: {', '.join(dupes)}")
    extra_cols = [c for c in sample[0] if c not in KEY_COLUMNS]

    trans = os.path.join(a.out, "for_transcribers")
    analyst = os.path.join(a.out, "analyst_only")
    for d in (os.path.join(trans, "audio"), os.path.join(trans, "templates"),
              os.path.join(analyst, "spans"), os.path.join(analyst, "lena"),
              os.path.join(analyst, "whisper"), os.path.join(analyst, "human"),
              os.path.join(analyst, "reports")):
        os.makedirs(d, exist_ok=True)

    # Existing clips keep their IDs; new ones get the next numbers, shuffled.
    key_path = os.path.join(analyst, "key.csv")
    key = {r["clip_id"]: r for r in C.read_csv(key_path)} if os.path.isfile(key_path) else {}
    used = [int(r["clip"][len(a.prefix):]) for r in key.values()
            if r["clip"].startswith(a.prefix) and r["clip"][len(a.prefix):].isdigit()]
    new = [r for r in sample if r["clip_id"] not in key]
    rng = random.Random(a.seed + len(key))
    numbers = list(range(max(used, default=0) + 1, max(used, default=0) + 1 + len(new)))
    rng.shuffle(numbers)
    n_double = int(round(a.double * len(new)))
    doubles = set(rng.sample(range(len(new)), n_double)) if new else set()
    for i, (row, number) in enumerate(zip(new, numbers)):
        key[row["clip_id"]] = {**row, "clip": f"{a.prefix}{number:03d}",
                               "double": "1" if i in doubles else "0"}

    say(f"[*] {len(sample)} clips in {os.path.basename(a.sample)}: "
        f"{len(sample) - len(new)} already in the package, {len(new)} new")
    its_cache = {}
    made = missing = no_lena = 0
    for row in sample:
        k = key[row["clip_id"]]
        clip = k["clip"]
        wav = os.path.join(trans, "audio", clip + ".wav")
        template = os.path.join(trans, "templates", clip + ".csv")
        start = float(row["start_sec"]) if row.get("start_sec") else None
        duration = float(row["duration_sec"]) if row.get("duration_sec") else 300.0

        if not os.path.isfile(wav):
            kind, src = find_audio(row, a.clips_dir, a.raw_dir)
            if kind is None:
                say(f"  [!] {row['clip_id']}: no audio. Looked for:\n        " + "\n        ".join(src))
                k["audio_source"] = "missing"
                missing += 1
                continue
            try:
                write_audio(kind, src, wav, start, duration)
            except (RuntimeError, ValueError, OSError) as e:
                say(f"  [!] {row['clip_id']}: couldn't write the clip from {src}: {e}")
                k["audio_source"] = "missing"
                missing += 1
                continue
            k["audio_source"] = "ready-cut clip" if kind == "clip" else "cut from recording"
        duration = C.wav_info(wav)[3]
        k["duration_sec"] = f"{duration:.2f}"

        if os.path.isfile(template):
            continue

        # LENA's segments in the clip, if its .its file and offset are known.
        segments, its_note = None, "no start_sec"
        if start is not None:
            its, its_note = find_lena(row, a.its_dir, a.lookup, its_cache)
            if its is not None:
                end = start + duration
                recorder = row.get("recording_id", "").split("_")[-1]
                if its.serial and recorder.isdigit() and its.serial.isdigit() \
                        and int(its.serial) != int(recorder):
                    say(f"  [!] {row['clip_id']}: {its_note} is from recorder {its.serial}, "
                        f"not {recorder}; check its_file")
                if end > its.duration + 1:
                    say(f"  [!] {row['clip_id']}: the clip ends at {end:.0f} s but {its_note} "
                        f"covers {its.duration:.0f} s; check start_sec")
                segments = [(max(0.0, s["start"] - start), min(duration, s["end"] - start), s["speaker"])
                            for s in its.segments if s["end"] > start and s["start"] < end]
                k["its_file"] = its_note
        k["lena"] = "yes" if segments is not None else "no"
        if segments is None:
            no_lena += 1
            say(f"  [~] {row['clip_id']} ({clip}): no LENA data ({its_note}); "
                f"{a.window:.0f}-second spans")
        else:
            C.write_csv(os.path.join(analyst, "lena", clip + ".csv"), C.LENA_COLUMNS,
                        [{"start_sec": f"{s:.2f}", "end_sec": f"{e:.2f}", "label": lab}
                         for s, e, lab in segments])

        spans = C.build_spans(segments, duration, a.span_max, a.merge_gap, a.min_span, a.window)
        template_rows, span_rows = [], []
        for n, sp in enumerate(spans, start=1):
            totals = C.lena_seconds(segments, sp["start"], sp["end"])
            template_rows.append({
                "clip": clip, "span": n,
                "start_sec": f"{sp['start']:.2f}", "end_sec": f"{sp['end']:.2f}",
                "start": C.clock(sp["start"]), "end": C.clock(sp["end"]),
                "lena_hint": C.lena_hint(totals, segments is not None),
                "status": "", "speech": "", "media": "", "notes": "",
            })
            span_rows.append({
                "clip": clip, "span": n,
                "start_sec": f"{sp['start']:.2f}", "end_sec": f"{sp['end']:.2f}",
                "kind": sp["kind"],
                "lena_speech_sec": f"{sum(v for l, v in totals.items() if l in C.SPEECH_LABELS):.2f}",
                "lena_labels": "; ".join(f"{l} {v:.2f}" for l, v in
                                         sorted(totals.items(), key=lambda kv: -kv[1]) if v >= 0.005),
            })
        C.write_csv(os.path.join(analyst, "spans", clip + ".csv"), C.SPAN_COLUMNS, span_rows)
        C.write_csv(template, C.TEMPLATE_COLUMNS, template_rows, excel=True)
        made += 1

    ordered = sorted(key.values(), key=lambda r: r["clip"])
    C.write_csv(key_path, KEY_COLUMNS + extra_cols, ordered)
    C.write_csv(os.path.join(analyst, "double_transcribe.csv"), ["clip"],
                [{"clip": r["clip"]} for r in ordered if r.get("double") == "1"
                 and r.get("audio_source") != "missing"])
    if os.path.isfile(a.instructions):
        shutil.copyfile(a.instructions, os.path.join(trans, "INSTRUCTIONS.txt"))
    with open(os.path.join(a.out, "README.txt"), "w", encoding="utf-8") as f:
        f.write(PACKAGE_README)

    n_clips = len([r for r in ordered if r.get("audio_source") != "missing"])
    say(f"[✓] {made} new templates; the package has {n_clips} clips"
        + (f"; {no_lena} without LENA data" if no_lena else "")
        + (f"; {missing} with no audio" if missing else ""))
    say(f"    {a.out}")
    if missing:
        sys.exit(1)


if __name__ == "__main__":
    main()
