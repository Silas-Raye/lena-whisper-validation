#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
common.py — helpers that make_package.py, sweep.py, and score.py share:
reading and writing clips, splitting a clip into spans for the templates,
the template and span-table formats, text normalization, and WER.

Nothing here needs more than Python 3.9, except norm_words() (needs
openai-whisper) and align() (needs numpy), which import them when called.
"""

import csv
import math
import os
import re
import wave

# ============================================================
# LENA LABELS
# ============================================================
# LENA's speaker labels that can contain words: adult female and male, the
# key child (the one wearing the recorder), other children, overlapping
# voices, and TV or other electronic sound. A label ending in F (FAF, CHF,
# ...) is the same thing heard faint or far away.
SPEECH_LABELS = {"FAN", "MAN", "CHN", "CXN", "OLN", "TVN",
                 "FAF", "MAF", "CHF", "CXF", "OLF", "TVF"}
LABEL_NAMES = {"FA": "woman", "MA": "man", "CH": "key child", "CX": "other child",
               "OL": "overlapping voices", "TV": "TV/electronic", "NO": "noise",
               "SI": "silence"}


def label_name(label):
    """'FAN' -> 'woman', 'CHF' -> 'key child (faint)'."""
    name = LABEL_NAMES.get(label[:2], label)
    return name + " (faint)" if label.endswith("F") and label != "SIL" else name


# ============================================================
# AUDIO
# ============================================================
RATE = 16000


def wav_info(path):
    """(sample rate, channels, sample width in bytes, seconds) of a .wav file."""
    with wave.open(path, "rb") as w:
        return w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes() / w.getframerate()


def is_whisper_ready(path):
    """True if the .wav is 16 kHz, mono, 16-bit: what Whisper reads directly."""
    rate, channels, width, _ = wav_info(path)
    return rate == RATE and channels == 1 and width == 2


def cut_wav(src, dst, start, duration):
    """Copy `duration` seconds of a 16 kHz mono 16-bit .wav, from `start`,
    into a new .wav. Returns the seconds written (less at the end of a file)."""
    with wave.open(src, "rb") as w:
        first = int(round(start * w.getframerate()))
        if first >= w.getnframes():
            raise ValueError(f"{os.path.basename(src)} is only "
                             f"{w.getnframes() / w.getframerate():.0f} s long; "
                             f"the clip starts at {start:.0f} s")
        w.setpos(first)
        frames = w.readframes(int(round(duration * w.getframerate())))
        params = w.getparams()
    tmp = dst + ".partial"
    with wave.open(tmp, "wb") as out:
        out.setparams(params)
        out.writeframes(frames)
    os.replace(tmp, dst)
    return len(frames) / (params.sampwidth * params.nchannels * params.framerate)


def load_audio(path):
    """A 16 kHz mono 16-bit .wav as a float32 numpy array, for Whisper."""
    import numpy as np
    with wave.open(path, "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (RATE, 1, 2):
            raise ValueError(f"{path} isn't 16 kHz mono 16-bit")
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


# ============================================================
# SPANS
# ============================================================
def clock(t):
    """Seconds -> 'm:ss.s', the way audio players show time."""
    m, s = divmod(max(t, 0.0), 60)
    return f"{int(m)}:{s:04.1f}"


def speech_regions(segments, duration, merge_gap=1.0, pad=0.0, labels=SPEECH_LABELS):
    """LENA's speech segments as [[start, end], ...] in seconds, padded by
    `pad`, kept inside the clip, and merged when less than `merge_gap` apart."""
    regions = []
    for start, end, label in sorted(segments):
        if label not in labels:
            continue
        start, end = max(0.0, start - pad), min(duration, end + pad)
        if end <= start:
            continue
        if regions and start - regions[-1][1] <= merge_gap:
            regions[-1][1] = max(regions[-1][1], end)
        else:
            regions.append([start, end])
    return regions


def build_spans(segments, duration, max_len=20.0, merge_gap=1.0, min_len=2.0, window=15.0):
    """Split a clip into spans that cover every second of it, in order.

    With LENA's segments for the clip ([(start, end, label)], in seconds from
    the start of the clip), span edges follow LENA: a stretch where LENA heard
    speech is one span, and so is a stretch where it didn't. Spans shorter
    than `min_len` join the one before; spans longer than `max_len` are split,
    at a LENA segment edge (usually a pause) near each cut when there is one.
    LENA only shapes the spans and the hint: every span, speech or not, gets
    transcribed.

    With no LENA data (segments is None), the spans are equal windows of
    about `window` seconds.

    Returns [{"start", "end", "kind"}] with kind "speech", "quiet", or "nolena".
    """
    if segments is None:
        n = max(1, int(math.ceil(duration / window - 1e-9)))
        edges = [round(duration * i / n, 2) for i in range(n + 1)]
        return [{"start": a, "end": b, "kind": "nolena"} for a, b in zip(edges, edges[1:])]

    pieces, t = [], 0.0
    for start, end in speech_regions(segments, duration, merge_gap):
        if start > t:
            pieces.append([t, start, "quiet"])
        pieces.append([max(start, t), end, "speech"])
        t = end
    if t < duration:
        pieces.append([t, duration, "quiet"])

    merged = []
    for p in pieces:
        if merged and (p[1] - p[0] < min_len or merged[-1][1] - merged[-1][0] < min_len):
            prev = merged[-1]
            prev[1] = p[1]
            prev[2] = "speech" if "speech" in (prev[2], p[2]) else "quiet"
        else:
            merged.append(list(p))

    edges_lena = sorted({round(x, 2) for s, e, _ in segments for x in (s, e) if 0 < x < duration})
    spans = []
    for start, end, kind in merged:
        n = max(1, int(math.ceil((end - start) / max_len - 1e-9)))
        cuts = []
        for i in range(1, n):
            target = start + (end - start) * i / n
            near = [x for x in edges_lena if abs(x - target) <= 3.0 and x > (cuts[-1] if cuts else start) + 1.0
                    and x < end - 1.0]
            cuts.append(min(near, key=lambda x: abs(x - target)) if near else target)
        edges = [start] + cuts + [end]
        spans += [{"start": round(a, 2), "end": round(b, 2), "kind": kind}
                  for a, b in zip(edges, edges[1:]) if b - a > 0.05]
    # make the spans exactly contiguous after rounding
    for a, b in zip(spans, spans[1:]):
        b["start"] = a["end"]
    if spans:
        spans[0]["start"], spans[-1]["end"] = 0.0, round(duration, 2)
    return spans


def lena_seconds(segments, start, end):
    """{label: seconds} of LENA's segments between start and end."""
    totals = {}
    for s, e, label in segments or []:
        overlap = min(end, e) - max(start, s)
        if overlap > 0:
            totals[label] = totals.get(label, 0.0) + overlap
    return totals


def lena_hint(totals, has_lena=True):
    """What the template tells the transcriber LENA heard in a span."""
    if not has_lena:
        return ""
    speech = sorted(((sec, lab) for lab, sec in totals.items()
                     if lab in SPEECH_LABELS and sec >= 0.2), reverse=True)
    if not speech:
        return "LENA: no speech"
    return "LENA heard: " + ", ".join(f"{label_name(lab)} {sec:.1f}s" for sec, lab in speech[:3])


# ============================================================
# FILES
# ============================================================
TEMPLATE_COLUMNS = ["clip", "span", "start_sec", "end_sec", "start", "end", "lena_hint",
                    "status", "speech", "media", "notes"]
SPAN_COLUMNS = ["clip", "span", "start_sec", "end_sec", "kind", "lena_speech_sec", "lena_labels"]
LENA_COLUMNS = ["start_sec", "end_sec", "label"]


def write_csv(path, columns, rows, excel=False):
    """Write rows (dicts) to a CSV. excel=True adds a byte-order mark so Excel
    opens the file as UTF-8 (curly quotes and accents stay intact)."""
    tmp = path + ".partial"
    with open(tmp, "w", newline="", encoding="utf-8-sig" if excel else "utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def read_csv(path):
    """Rows of a CSV as dicts with stripped, lower-case column names. Reads
    UTF-8 (with or without a byte-order mark), falling back to Windows-1252,
    which Excel sometimes saves; accepts ; as the separator, which Excel uses
    in some regions."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with open(path, newline="", encoding=encoding) as f:
                text = f.read()
            break
        except UnicodeDecodeError:
            continue
    first = text.split("\n", 1)[0]
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    rows = list(reader)
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    return [{h: (r[i].strip() if i < len(r) else "") for i, h in enumerate(header)}
            for r in rows[1:] if any(c.strip() for c in r)]


def read_table(path):
    """A filled-in template: .csv, or .xlsx if openpyxl is installed."""
    if path.lower().endswith(".xlsx"):
        try:
            import openpyxl
        except ImportError:
            raise SystemExit(f"[X] {path} is an Excel file. Save it as CSV (UTF-8), or "
                             "install openpyxl: pip install openpyxl")
        ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
        rows = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
        header = [h.strip().lower() for h in rows[0]]
        return [{h: (r[i].strip() if i < len(r) else "") for i, h in enumerate(header)}
                for r in rows[1:] if any(c.strip() for c in r)]
    return read_csv(path)


def read_lena(path):
    """[(start, end, label)] from analyst_only/lena/<clip>.csv, or None."""
    if not os.path.isfile(path):
        return None
    return [(float(r["start_sec"]), float(r["end_sec"]), r["label"]) for r in read_csv(path)]


def read_spans(path):
    """The span table from analyst_only/spans/<clip>.csv."""
    out = []
    for r in read_csv(path):
        out.append({"span": int(r["span"]), "start": float(r["start_sec"]),
                    "end": float(r["end_sec"]), "kind": r["kind"],
                    "lena_speech_sec": float(r["lena_speech_sec"] or 0)})
    return out


# ============================================================
# TEXT AND WER
# ============================================================
_NORMALIZER = None
_CACHE = {}

LABEL_RE = re.compile(r"(?<![\w'])(S\w{3,7}\s?[0-9?]+|Speakers?|TV|Radio|Movie|Toy|Google Home)\s*:")
ACTION_RE = re.compile(r"\*[^*]*\*")
STRETCH_RE = re.compile(r"([A-Za-z])\1{2,}")
UNCLEAR_RE = re.compile(r"\bx{2,}\b", re.I)


def clean(text):
    """Transcription conventions to strip before normalizing: speaker labels
    ("Speaker 1:", "TV:"), *actions*, ellipses and dashes, and stretched
    letters ("Ewwww" -> "Ew")."""
    t = LABEL_RE.sub(" ", text)
    t = ACTION_RE.sub(" ", t)
    t = t.replace("…", " ").replace("--", " ").replace("—", " ")
    return STRETCH_RE.sub(r"\1", t)


def norm_words(text):
    """Words for scoring, via Whisper's English normalizer, the standard one
    for reporting Whisper WER: it drops [bracketed] and (parenthesized) text,
    fillers (um, uh, hmm), and punctuation, and standardizes numbers,
    spellings, and contractions."""
    global _NORMALIZER
    if not text or not text.strip():
        return []
    if text not in _CACHE:
        if _NORMALIZER is None:
            from whisper.normalizers import EnglishTextNormalizer
            _NORMALIZER = EnglishTextNormalizer()
        _CACHE[text] = _NORMALIZER(clean(text)).split()
    return _CACHE[text]


def align(ref, hyp):
    """Word alignment of two word lists: (substitutions, deletions, insertions)."""
    import numpy as np
    n, m = len(ref), len(hyp)
    if n == 0 or m == 0:
        return 0, n, m
    vocab = {w: i for i, w in enumerate(set(ref) | set(hyp))}
    r = np.array([vocab[w] for w in ref])
    h = np.array([vocab[w] for w in hyp])
    D = np.zeros((n + 1, m + 1), dtype=np.int32)
    D[0] = np.arange(m + 1)
    cols = np.arange(m + 1)
    row = np.empty(m + 1, dtype=np.int32)
    for i in range(1, n + 1):
        row[0] = i
        row[1:] = np.minimum(D[i - 1, :-1] + (h != r[i - 1]), D[i - 1, 1:] + 1)
        # insertions along the row: D[i, j] = min over k <= j of row[k] + (j - k)
        D[i] = np.minimum.accumulate(row - cols) + cols
    i, j, S, Dl, I = n, m, 0, 0, 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and D[i, j] == D[i - 1, j - 1] + (ref[i - 1] != hyp[j - 1]):
            S += ref[i - 1] != hyp[j - 1]
            i, j = i - 1, j - 1
        elif i > 0 and D[i, j] == D[i - 1, j] + 1:
            Dl, i = Dl + 1, i - 1
        else:
            I, j = I + 1, j - 1
    return S, Dl, I
