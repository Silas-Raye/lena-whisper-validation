#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_legacy_reference.py — turn the 2026 corrected spreadsheets into a
reference that score.py can use (score.sh --legacy).

Run it on your own computer, where the spreadsheets are. It needs pandas and
openpyxl (pip install pandas openpyxl):
    python tools/make_legacy_reference.py "<path to>/validationSample" legacy_reference.csv

The annotators edited a Whisper transcript, one row per Whisper segment, and
followed different conventions. Each row's reference is rebuilt with these
rules; the status column says which applied:
    corrected      Corrected_Text is filled in: it's the reference
    accepted       no correction: the annotator accepted Whisper's text
    deleted        Notes say the line wasn't said ("not present", ...)
    unscorable     "audio muffled", or the correction is only an inaudible
                   marker: the row is left out of the reference and of
                   every Whisper transcript, by time
    added          speech that SB wrote in Notes, in quotes
    +doubled       Notes say the line was said twice
Because the annotators started from Whisper's text, this reference favors
transcripts that resemble it. Scores against it are provisional.

Writes one row per spreadsheet row: clip_id, row, start_sec and end_sec
(seconds from the start of the clip), status, ref_text, hyp_text (the text the
annotator started from), annotator.
"""

import glob
import os
import re
import sys

import pandas as pd

# Windows consoles can't print every character (such as the check marks in
# messages); print a substitute rather than stop.
try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass


LABEL_RE = re.compile(r"(?<![\w'])(S\w{3,7}\s?[0-9?]+|Speakers?|TV|Radio|Movie|Toy|Google Home)\s*:")
INAUDIBLE_RE = re.compile(
    r"^\W*(?:[\[(]\s*)?(inaudible|indistinct|undiscernible|unintelligible|"
    r"indiscernible|unclear|muffled)[^\])]*[\])]?\W*$", re.I)
DELETE_NOTE_RE = re.compile(
    r"not present|line is not said|not said twice|no more spoon|spoon only clicks", re.I)
DOUBLE_NOTE_RE = re.compile(r"says (this )?(two|2) times", re.I)
QUOTE_RE = re.compile(r"[\"“]([^\"”]+)[\"”]")


def clip_and_annotator(fname):
    """1053_052003_audiochunk63_LENAcut_CorrectedLJ.xlsx -> (1053_052003_audiochunk_63_LENAcut, LJ)"""
    m = re.match(r"(\d+)_(\d+)_audiochunk_?(\d+)_LENAcut_Correct(?:ed|ion)([A-Z]+)", fname)
    if not m:
        raise ValueError(f"can't read the clip name in {fname}")
    return f"{m.group(1)}_{m.group(2)}_audiochunk_{m.group(3)}_LENAcut", m.group(4)


def load_sheet(path):
    raw = pd.read_excel(path, header=None, dtype=object)
    header = [str(x).strip() if pd.notna(x) else "" for x in raw.iloc[0].tolist()]
    body = raw.iloc[1:].reset_index(drop=True)

    def col(name):
        return body.iloc[:, header.index(name)].apply(lambda v: "" if pd.isna(v) else str(v).strip())

    clip, annotator = clip_and_annotator(os.path.basename(path))
    df = pd.DataFrame({
        "clip_id": clip, "annotator": annotator, "row": range(len(body)),
        "sheet_clip": col("Audio_Clip"),
        "start_ms": pd.to_numeric(body.iloc[:, header.index("Start_time")], errors="coerce"),
        "end_ms": pd.to_numeric(body.iloc[:, header.index("End_time")], errors="coerce"),
        "text": col("Text"), "corrected": col("Corrected_Text"), "notes": col("Notes"),
    })
    # drop empty rows and a row pasted in from another clip's sheet
    df = df[~((df.text == "") & (df.corrected == "") & (df.notes == "") & df.start_ms.isna())]
    return df[(df.sheet_clip == "") | (df.sheet_clip == clip)]


def reference(text, corr, notes, annotator):
    notes_l = notes.lower().strip()
    if corr:
        ref, status = corr, "corrected"
    elif DELETE_NOTE_RE.search(notes):
        ref, status = "", "deleted"
    else:
        ref, status = text, "accepted"
    if notes_l == "audio muffled" or (corr and INAUDIBLE_RE.match(LABEL_RE.sub(" ", corr).strip())):
        status = "unscorable"
    elif annotator == "SB":
        # SB wrote missed speech in Notes, in quotes; other annotators' quotes
        # are comments on their own correction.
        extra = QUOTE_RE.findall(notes)
        if extra:
            ref = (ref + " " + " ".join(extra)).strip()
            status = "added" if not text else status + "+added"
    if DOUBLE_NOTE_RE.search(notes):
        ref = (ref + " " + ref).strip()
        status += "+doubled"
    return ref, status


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    folder, out = sys.argv[1], sys.argv[2]
    sheets = sorted(glob.glob(os.path.join(folder, "correctedChunks", "*.xlsx")))
    markers = pd.read_csv(os.path.join(folder, "clip_markers.csv")).set_index("clip").start_time
    df = pd.concat([load_sheet(p) for p in sheets], ignore_index=True)
    refs = [reference(t, c, n, a) for t, c, n, a in zip(df.text, df.corrected, df.notes, df.annotator)]
    df["ref_text"] = [r for r, _ in refs]
    df["status"] = [s for _, s in refs]
    # Sheet times are milliseconds into the stitched file of every clip.
    offset = df.clip_id.map(markers)
    df["start_sec"] = ((df.start_ms - offset) / 1000).round(2)
    df["end_sec"] = ((df.end_ms - offset) / 1000).round(2)
    df["hyp_text"] = df.text
    df[["clip_id", "row", "start_sec", "end_sec", "status", "ref_text", "hyp_text", "annotator"]] \
        .to_csv(out, index=False, encoding="utf-8")
    print(f"[✓] {df.clip_id.nunique()} clips, {len(df)} rows -> {out}")
    print("    " + ", ".join(f"{k} {v}" for k, v in df.status.value_counts().items()))


if __name__ == "__main__":
    main()
