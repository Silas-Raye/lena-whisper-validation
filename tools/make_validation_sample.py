#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_validation_sample.py — write sample.csv for the validation clips that
were cut in May 2026 (Dylan's validationSample folder).

Run it on your own computer, where that folder is, then copy sample.csv to
ACCRE (print_input_commands.sh prints the command):
    python tools/make_validation_sample.py "<path to>/validationSample" sample.csv

It needs Python 3.9 or newer and nothing else. It reads:
    filesToValidate.csv              each clip's recording, LENA block, and the
                                     length of the recording's first block
    wavsToSearchForValidation.csv    each recording's .its file name
    lena_transcript_validation_sample_*.csv   the sample's groups (age, AWC
                                     level, education group, ...)
    validation_chunks/               which clips were cut

How the clips were cut (0_compile_session_5min_chunks_server_search.py):
filesToValidate.csv names a clip like 1017_029796_audiochunk_135, where 135
counts LENA blocks from 0 after the first, partial one. The clip starts at
135 * 300 + offsetClipOne seconds into the recording and is 300 seconds long,
and its file is named 1017_029796_audiochunk_136_LENAcut.wav.
"""

import csv
import glob
import os
import sys

# Windows consoles can't print every character (such as the check marks in
# messages); print a substitute rather than stop.
try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass


GROUP_COLUMNS = ["timepoint", "day_index", "AWC_level", "edu_group", "time_band",
                 "dow_type", "child_sex"]


def read(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    folder, out = sys.argv[1], sys.argv[2]
    include_missing = "--include-missing" in sys.argv

    files = read(os.path.join(folder, "filesToValidate.csv"))
    wavs = read(os.path.join(folder, "wavsToSearchForValidation.csv"))
    samples = sorted(glob.glob(os.path.join(folder, "lena_transcript_validation_sample_*.csv")))
    sample = read(samples[-1]) if samples else []
    cut = {os.path.basename(p)[:-4] for p in glob.glob(os.path.join(folder, "validation_chunks", "*.wav"))}

    its_by_rec = {}
    for w in wavs:
        name = (w.get("ITS_File_Name") or "").strip()
        if w.get("specifier", "").isdigit():
            its_by_rec[(w["id"], int(w["specifier"]))] = "" if name == "NA" else name
    groups = {(s["id"], s["timepoint"]): s for s in sample}

    rows, missing = [], []
    for f in files:
        rec, k = f["wavChunkName"].rsplit("_audiochunk_", 1)
        child, recorder = rec.split("_", 1)
        clip_id = f"{rec}_audiochunk_{int(k) + 1}_LENAcut"
        if clip_id not in cut and not include_missing:
            missing.append(clip_id)
            continue
        row = {
            "clip_id": clip_id,
            "recording_id": rec,
            "start_sec": int(k) * 300 + int(float(f["offsetClipOne"])),
            "duration_sec": 300,
            "its_file": its_by_rec.get((child, int(recorder)), ""),
        }
        g = groups.get((child, f["age"]), {})
        for col in GROUP_COLUMNS:
            row[col] = g.get(col, f["age"] if col == "timepoint" else "")
        rows.append(row)

    with open(out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["clip_id", "recording_id", "start_sec", "duration_sec",
                                                "its_file"] + GROUP_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"[✓] {len(rows)} clips -> {out}")
    print(f"    {sum(1 for r in rows if r['its_file'])} with an .its file name, "
          f"{sum(1 for r in rows if r['edu_group'])} with the sample's groups")
    if missing:
        print(f"[~] Left out {len(missing)} clips that aren't in validation_chunks "
              f"(add --include-missing to keep them):")
        for m in missing:
            print(f"      {m}")


if __name__ == "__main__":
    main()
