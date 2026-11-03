#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
score.py — compare every Whisper run in a package with the human transcripts.

score.sh runs this on ACCRE; by hand:
    python score.py --package <package folder>
    python score.py --package <package folder> --legacy legacy_reference.csv

The reference
  * Human transcripts are the filled-in templates in analyst_only/human/,
    named <clip>_<initials>.csv (V001_AR.csv). When two people transcribed a
    clip, the first by initials is the reference and the pair measures how
    much transcribers agree with each other.
  * Spans marked "unclear", or with "xxx" for words that couldn't be made
    out, are left out of both the reference and Whisper's transcript, by time.
  * --legacy instead scores against the corrections made before this repo
    (tools/make_legacy_reference.py). Those were made by editing Whisper's
    output, so they favor runs that resemble it: treat them as provisional.

Post-filters, applied to each run's segments before scoring (no GPU needed):
    raw                 none
    norepeat            drop the 3rd and later copies of a segment that
                        repeats the one before it
    lenacheck           drop a segment when LENA heard no speech within a
                        second of it AND Whisper itself was unsure of it
                        (no_speech_prob >= 0.5 or avg_logprob <= -0.8):
                        LENA as a clue, not the final word
    norepeat+lenacheck  both

Clips are split once into tuning clips (60%) and held-out clips (40%), by a
hash of each clip's recording name. Choose settings by their tuning WER and
report the chosen one's held-out WER: choosing and reporting on the same
clips overstates the gain. "vs baseline" puts a 95% interval on each run's
tuning-WER difference from the baseline by resampling clips (paired
bootstrap): an interval that includes 0 is not a clear difference.

Writes analyst_only/reports/<date_time>/ with report.md, runs.csv (one row
per run and filter), and clips.csv (one row per clip, run, and filter).
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sys
from datetime import datetime

import common as C

# Windows consoles can't print every character (such as the check marks in
# messages); print a substitute rather than stop.
try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass


FILTERS = ["raw", "norepeat", "lenacheck", "norepeat+lenacheck"]
STATUS = {"speech": "speech", "s": "speech", "yes": "speech", "y": "speech", "words": "speech",
          "none": "none", "n": "none", "no": "none", "nothing": "none", "0": "none",
          "no speech": "none", "silence": "none",
          "unclear": "unclear", "u": "unclear", "?": "unclear", "inaudible": "unclear",
          "unintelligible": "unclear"}
NOT_STRATA = {"clip", "clip_id", "recording_id", "start_sec", "duration_sec", "its_file", "lena",
              "double", "audio_source", "id", "day_index", "recordingdate", "starttime_ct",
              "endtime_ct", "duration_secs", "awc_count", "cv_count", "ct_count", "mom_age",
              "mom_edu_num", "income_needs", "stratum", "edu_weight", "row_weight", "income_txt",
              "mom_edu_txt", "dayofweek"}


def say(msg=""):
    print(msg, flush=True)


def is_heldout(clip_id):
    return int(hashlib.md5(clip_id.encode()).hexdigest(), 16) % 5 < 2


# ============================================================
# HUMAN TRANSCRIPTS
# ============================================================
def parse_human(rows, spans):
    """The filled-in rows matched to the template's spans, by span number (or
    by order if Excel mangled the numbers). Returns (annotations, problem)."""
    by_span = {}
    for r in rows:
        try:
            by_span[int(float(r.get("span", "")))] = r
        except ValueError:
            pass
    numbers = {s["span"] for s in spans}
    if set(by_span) != numbers:
        if len(rows) != len(spans):
            return None, f"has {len(rows)} rows, but the template has {len(spans)}"
        by_span = {s["span"]: r for s, r in zip(spans, rows)}
    out, unmarked = [], 0
    for s in spans:
        r = by_span[s["span"]]
        speech, media = r.get("speech", ""), r.get("media", "")
        raw = r.get("status", "").strip().lower()
        status = STATUS.get(raw)
        if status is None:
            unmarked += 1
            status = "speech" if (speech or media) else "none"
        out.append({**s, "status": status, "speech": speech, "media": media})
    return out, (f"{unmarked} rows with no status; counted as none or speech by their text"
                 if unmarked else "")


def load_human(analyst, spans_by_clip):
    """{clip: {initials: annotations}} from analyst_only/human/."""
    human, problems = {}, []
    files = sorted(glob.glob(os.path.join(analyst, "human", "*.csv"))
                   + glob.glob(os.path.join(analyst, "human", "*.xlsx")))
    for path in files:
        stem = os.path.splitext(os.path.basename(path))[0]
        m = re.match(r"^([A-Za-z]+\d+)(?:[ _\-]+(.+))?$", stem)
        if not m or m.group(1).upper() not in spans_by_clip:
            problems.append(f"{os.path.basename(path)}: not named <clip>_<initials>, or no such clip")
            continue
        clip, who = m.group(1).upper(), (m.group(2) or "?").upper()
        ann, note = parse_human(C.read_table(path), spans_by_clip[clip])
        if ann is None:
            problems.append(f"{os.path.basename(path)}: {note}; skipped")
            continue
        if note:
            problems.append(f"{os.path.basename(path)}: {note}")
        human.setdefault(clip, {})[who] = ann
    return human, problems


def human_reference(ann, live_only=False):
    """(per-span reference words or None if left out, left-out intervals)."""
    per_span, excluded = [], []
    for a in ann:
        text = (a["speech"] + " " + a["media"]).strip()
        if a["status"] == "unclear" or C.UNCLEAR_RE.search(text) or (live_only and a["media"].strip()):
            per_span.append(None)
            excluded.append((a["start"], a["end"]))
        else:
            per_span.append(C.norm_words(text))
    return per_span, excluded


# ============================================================
# WHISPER RUNS
# ============================================================
def lena_near(seg, lena):
    """True if LENA heard speech within a second of the segment."""
    return any(lab in C.SPEECH_LABELS and e > seg["start"] - 1 and s < seg["end"] + 1
               for s, e, lab in lena)


def filtered(segments, filt, lena):
    """The segments that a post-filter keeps."""
    segs = list(segments)
    if "norepeat" in filt:
        kept, prev, run = [], None, 0
        for s in segs:
            norm = " ".join(C.norm_words(s.get("text", "")))
            run = run + 1 if norm and norm == prev else 1
            prev = norm or prev
            if run < 3:
                kept.append(s)
        segs = kept
    if "lenacheck" in filt and lena is not None:
        segs = [s for s in segs if lena_near(s, lena)
                or (s.get("no_speech_prob", 0) < 0.5 and s.get("avg_logprob", 0) > -0.8)]
    return segs


def hyp_by_span(segments, spans):
    """Whisper's words in each span: a word goes in the span that holds its
    middle (a segment's middle when there are no word times). Each span's
    text is normalized as a whole, like the reference."""
    texts = [[] for _ in spans]
    starts = [s["start"] for s in spans]

    def index(t):
        i = 0
        while i + 1 < len(starts) and t >= starts[i + 1]:
            i += 1
        return i

    for seg in segments:
        if seg.get("words"):
            for w in seg["words"]:
                texts[index((w["start"] + w["end"]) / 2)].append(w["word"])
        else:
            texts[index((seg["start"] + seg["end"]) / 2)].append(" " + seg.get("text", ""))
    return [C.norm_words("".join(t)) for t in texts]


def load_runs(analyst):
    """{run: {clip: Whisper's result}}."""
    runs = {}
    for folder in sorted(glob.glob(os.path.join(analyst, "whisper", "*__*"))):
        name = os.path.basename(folder)
        for path in glob.glob(os.path.join(folder, "*.json")):
            try:
                with open(path, encoding="utf-8") as f:
                    runs.setdefault(name, {})[os.path.basename(path)[:-5]] = json.load(f)
            except ValueError:
                say(f"[!] {path} is damaged (a job stopped while writing it); delete it and rerun")
    return runs


# ============================================================
# SCORING
# ============================================================
def score_clip(ref_spans, hyp_spans, ann=None):
    """WER counts for one clip, from per-span reference and hypothesis words."""
    ref = [w for r in ref_spans if r is not None for w in r]
    hyp = [w for r, h in zip(ref_spans, hyp_spans) if r is not None for w in h]
    S, D, I = C.align(ref, hyp)
    out = {"N": len(ref), "S": S, "D": D, "I": I, "hyp_words": len(hyp)}
    if ann is not None:
        quiet = [i for i, a in enumerate(ann) if ref_spans[i] is not None and not ref_spans[i]
                 and a["status"] == "none"]
        out["quiet_sec"] = sum(ann[i]["end"] - ann[i]["start"] for i in quiet)
        out["halluc_words"] = sum(len(hyp_spans[i]) for i in quiet)
        out["missed_words"] = sum(len(r) for r, h in zip(ref_spans, hyp_spans) if r and not h)
    return out


def bootstrap(errs_a, n_a, errs_b, n_b, reps=2000, seed=0):
    """95% interval of WER(b) - WER(a), in points, resampling clips."""
    import numpy as np
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(errs_a), size=(reps, len(errs_a)))
    ea, na, eb, nb = (np.asarray(x, dtype=float) for x in (errs_a, n_a, errs_b, n_b))
    diff = 100 * (eb[idx].sum(1) / nb[idx].sum(1) - ea[idx].sum(1) / na[idx].sum(1))
    return np.percentile(diff, [2.5, 97.5])


def pct(e, n):
    return 100.0 * e / n if n else float("nan")


def md_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def vs_base(s):
    """'+1.2 [-0.4, +2.9]': difference from the baseline with its 95% interval."""
    if "vs_base" not in s:
        return "–"
    return f"{s['vs_base']:+.1f} [{s['ci_lo']:+.1f}, {s['ci_hi']:+.1f}]"


def kappa(pairs):
    """Cohen's kappa for two raters' yes/no calls."""
    n = len(pairs)
    if not n:
        return float("nan")
    agree = sum(a == b for a, b in pairs) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    return (agree - expected) / (1 - expected) if expected < 1 else 1.0


def main():
    ap = argparse.ArgumentParser(description="Score a package's Whisper runs.")
    ap.add_argument("--package", required=True)
    ap.add_argument("--legacy", default="", help="legacy_reference.csv: score against the old corrections")
    ap.add_argument("--baseline", default="large-v2__prod", help="the run to compare the others with")
    ap.add_argument("--min-coverage", type=float, default=0.9,
                    help="compare runs that have transcribed at least this share of the clips")
    ap.add_argument("--out", default="", help="report folder (default: analyst_only/reports/<now>)")
    a = ap.parse_args()

    analyst = os.path.join(a.package, "analyst_only")
    key = {r["clip"]: r for r in C.read_csv(os.path.join(analyst, "key.csv"))}
    by_id = {r["clip_id"]: r["clip"] for r in key.values()}
    spans = {c: C.read_spans(os.path.join(analyst, "spans", c + ".csv")) for c in key
             if os.path.isfile(os.path.join(analyst, "spans", c + ".csv"))}
    lena = {c: C.read_lena(os.path.join(analyst, "lena", c + ".csv")) for c in key}
    runs = load_runs(analyst)
    notes = []

    # ---- the reference, per clip ---------------------------------------------
    legacy = bool(a.legacy)
    if legacy:
        rows = [r for r in C.read_csv(a.legacy) if r["clip_id"] in by_id]
        refs = {}
        for r in sorted(rows, key=lambda r: (r["clip_id"], int(r["row"]))):
            refs.setdefault(by_id[r["clip_id"]], []).append(r)
        source = f"the old corrections ({os.path.basename(a.legacy)}); provisional"
        human = {}
    else:
        human, problems = load_human(analyst, spans)
        notes += problems
        refs = {c: human[c][sorted(human[c])[0]] for c in human}
        source = "human transcripts in analyst_only/human/"
    if not refs:
        sys.exit("[X] No references to score against. Put the filled-in templates in "
                 f"{os.path.join(analyst, 'human')} (named like V001_AR.csv), or use --legacy.")

    def reference(clip, live_only=False):
        if legacy:
            ok = [r for r in refs[clip] if r["status"] != "unscorable"]
            bad = [(float(r["start_sec"]), float(r["end_sec"])) for r in refs[clip]
                   if r["status"] == "unscorable" and r["start_sec"] and r["end_sec"]]
            return [w for r in ok for w in C.norm_words(r["ref_text"])], bad
        per_span, excluded = human_reference(refs[clip], live_only)
        return per_span, excluded

    # legacy "original": the transcript the annotators corrected
    if legacy:
        runs["original (what annotators corrected)"] = {
            c: {"segments": [{"text": r["hyp_text"], "start": float(r["start_sec"] or 0),
                              "end": float(r["end_sec"] or 0)} for r in refs[c]
                             if r["status"] != "unscorable"],
                "elapsed_s": None, "duration_s": None}
            for c in refs}

    # ---- which runs and clips to compare ---------------------------------------
    candidates = set(refs)
    coverage = {run: len(candidates & set(res)) / len(candidates) for run, res in runs.items()}
    compared = [run for run in runs if coverage[run] >= a.min_coverage]
    in_progress = [run for run in runs if run not in compared]
    clips = sorted(candidates.intersection(*[set(runs[r]) for r in compared])) if compared else []
    if not clips:
        sys.exit("[X] No clip has both a reference and a transcript from every run that is "
                 f"{100 * a.min_coverage:.0f}% done. Runs: "
                 + ", ".join(f"{r} {100 * coverage[r]:.0f}%" for r in runs))
    baseline = a.baseline if a.baseline in compared else compared[0]
    if baseline != a.baseline:
        notes.append(f"{a.baseline} isn't done, so runs are compared with {baseline}")

    # ---- score -----------------------------------------------------------------
    say(f"[*] Scoring {len(compared)} runs x {len(FILTERS)} filters on {len(clips)} clips "
        f"against {source}")
    results = []
    for run in compared:
        for filt in FILTERS:
            if filt != "raw" and "lenacheck" in filt and run.startswith("original"):
                continue
            for clip in clips:
                res = runs[run][clip]
                segs = filtered(res.get("segments", []), filt, lena.get(clip))
                if legacy:
                    ref_words, bad = reference(clip)
                    hyp = [w for s in segs
                           if not any(lo <= (s["start"] + s["end"]) / 2 <= hi for lo, hi in bad)
                           for w in C.norm_words(s.get("text", ""))]
                    S, D, I = C.align(ref_words, hyp)
                    row = {"N": len(ref_words), "S": S, "D": D, "I": I, "hyp_words": len(hyp)}
                    live = None
                else:
                    per_span, _ = reference(clip)
                    hyp_spans = hyp_by_span(segs, spans[clip])
                    row = score_clip(per_span, hyp_spans, refs[clip])
                    live_ref, _ = reference(clip, live_only=True)
                    live = score_clip(live_ref, hyp_spans)
                row.update(run=run, filter=filt, clip=clip, clip_id=key[clip]["clip_id"],
                           heldout=is_heldout(key[clip]["clip_id"]),
                           secs=res.get("elapsed_s"), audio_sec=res.get("duration_s"))
                if live:
                    row.update(live_N=live["N"], live_E=live["S"] + live["D"] + live["I"])
                results.append(row)
        say(f"    {run}")

    # ---- summarize ---------------------------------------------------------------
    def total(rows, k):
        return sum(r.get(k) or 0 for r in rows)

    groups = {}
    for r in results:
        groups.setdefault((r["run"], r["filter"]), []).append(r)
    base = {r["clip"]: r for r in groups[(baseline, "raw")]}
    summary = []
    for (run, filt), rows in groups.items():
        tune = [r for r in rows if not r["heldout"]]
        held = [r for r in rows if r["heldout"]]
        e_t = [r["S"] + r["D"] + r["I"] for r in tune]
        s = {
            "run": run, "filter": filt,
            "model": run.split("__")[0], "config": run.split("__")[-1],
            "tune_wer": pct(sum(e_t), total(tune, "N")),
            "sub": pct(total(tune, "S"), total(tune, "N")),
            "del": pct(total(tune, "D"), total(tune, "N")),
            "ins": pct(total(tune, "I"), total(tune, "N")),
            "heldout_wer": pct(total(held, "S") + total(held, "D") + total(held, "I"), total(held, "N")),
            "all_wer": pct(total(rows, "S") + total(rows, "D") + total(rows, "I"), total(rows, "N")),
            "live_wer": pct(total(rows, "live_E"), total(rows, "live_N")) if not legacy else float("nan"),
            "halluc_per_min": (60 * total(rows, "halluc_words") / total(rows, "quiet_sec")
                               if not legacy and total(rows, "quiet_sec") else float("nan")),
            "missed_pct": pct(total(rows, "missed_words"), total(rows, "N")) if not legacy else float("nan"),
            "sec_per_clip": (total(rows, "secs") / len(rows)) if rows[0].get("secs") is not None else float("nan"),
            "speed_x": (total(rows, "audio_sec") / total(rows, "secs")
                        if rows[0].get("secs") and total(rows, "secs") else float("nan")),
        }
        bt = [base[r["clip"]] for r in tune]
        if tune and total(bt, "N"):
            lo, hi = bootstrap([b["S"] + b["D"] + b["I"] for b in bt], [b["N"] for b in bt],
                               e_t, [r["N"] for r in tune])
            s["vs_base"] = s["tune_wer"] - pct(sum(b["S"] + b["D"] + b["I"] for b in bt), total(bt, "N"))
            s["ci_lo"], s["ci_hi"] = lo, hi
        summary.append(s)
    # With very few clips the tuning set can be empty; then rank by all clips.
    summary.sort(key=lambda s: s["tune_wer"] if s["tune_wer"] == s["tune_wer"] else s["all_wer"])
    best = summary[0]

    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = a.out or os.path.join(analyst, "reports", stamp + ("_legacy" if legacy else ""))
    os.makedirs(out, exist_ok=True)
    cols = ["run", "filter", "model", "config", "tune_wer", "sub", "del", "ins", "heldout_wer",
            "all_wer", "live_wer", "halluc_per_min", "missed_pct", "sec_per_clip", "speed_x",
            "vs_base", "ci_lo", "ci_hi"]
    f2 = lambda x: "" if x is None or x != x else (f"{x:.2f}" if isinstance(x, float) else x)
    C.write_csv(os.path.join(out, "runs.csv"), cols, [{k: f2(s.get(k)) for k in cols} for s in summary])
    clip_cols = ["run", "filter", "clip", "clip_id", "heldout", "N", "S", "D", "I", "hyp_words",
                 "halluc_words", "quiet_sec", "missed_words", "live_N", "live_E", "secs", "audio_sec"]
    C.write_csv(os.path.join(out, "clips.csv"), clip_cols,
                [{k: f2(r.get(k)) for k in clip_cols} for r in results])

    # ---- the report ---------------------------------------------------------------
    n1 = lambda x: "–" if x is None or x != x else f"{x:.1f}"
    lines = [f"# Whisper runs scored against {source}", "",
             f"{datetime.now():%Y-%m-%d %H:%M}. {len(clips)} clips "
             f"({sum(not is_heldout(key[c]['clip_id']) for c in clips)} tuning, "
             f"{sum(is_heldout(key[c]['clip_id']) for c in clips)} held out), "
             f"{sum(len(reference(c)[0]) if legacy else sum(len(w) for w in reference(c)[0] if w) for c in clips)} "
             f"reference words. WER and its parts are percentages of reference words. "
             f"Baseline: {baseline}, raw.", ""]
    if not legacy:
        excl = sum(a_["end"] - a_["start"] for c in clips for a_, w in zip(refs[c], reference(c)[0]) if w is None)
        lines += [f"Left out as unclear: {excl / 60:.1f} of {sum(s['end'] for s in (spans[c][-1] for c in clips)) / 60:.0f} minutes.", ""]
    lines += ["## Best settings for each model", "",
              "The lowest tuning WER for each model, any setting and filter. Speed is audio "
              "seconds per processing second on the GPU the run used.", ""]
    per_model = {}
    for s in summary:
        per_model.setdefault(s["model"], s)
    lines.append(md_table(
        ["model", "setting", "filter", "tuning WER", "held-out WER", "sec/clip", "speed", "vs baseline [95% CI]"],
        [[m, s["config"], s["filter"], n1(s["tune_wer"]), n1(s["heldout_wer"]), n1(s["sec_per_clip"]),
          (n1(s["speed_x"]) + "x") if s["speed_x"] == s["speed_x"] else "–",
          vs_base(s)]
         for m, s in per_model.items()]))
    lines += ["", "## Every run", "",
              "sub, del, ins: substitutions, deletions (missed words), insertions (extra words). "
              "made-up/min: Whisper words per minute of spans the transcriber marked as having no "
              "speech. missed: reference words in spans where Whisper wrote nothing. "
              "live-only: WER on spans without TV/radio/toy speech.", ""]
    lines.append(md_table(
        ["run", "filter", "tuning WER", "sub", "del", "ins", "held-out", "live-only", "made-up/min",
         "missed %", "sec/clip", "vs baseline [95% CI]"],
        [[s["run"], s["filter"], n1(s["tune_wer"]), n1(s["sub"]), n1(s["del"]), n1(s["ins"]),
          n1(s["heldout_wer"]), n1(s["live_wer"]), n1(s["halluc_per_min"]), n1(s["missed_pct"]),
          n1(s["sec_per_clip"]),
          vs_base(s)]
         for s in summary]))
    if in_progress:
        lines += ["", "Not compared yet (share of clips transcribed): "
                  + ", ".join(f"{r} {100 * coverage[r]:.0f}%" for r in in_progress)]

    if not legacy:
        # ---- transcriber agreement
        pairs = [(c, sorted(human[c])) for c in clips if len(human.get(c, {})) > 1]
        lines += ["", "## Transcribers vs. each other", ""]
        if pairs:
            E = N = 0
            calls = []
            for c, whos in pairs:
                ref_a, _ = human_reference(human[c][whos[0]])
                for who in whos[1:]:
                    ref_b, _ = human_reference(human[c][who])
                    both = [(x, y) for x, y in zip(ref_a, ref_b) if x is not None and y is not None]
                    S, D, I = C.align([w for x, _ in both for w in x], [w for _, y in both for w in y])
                    E, N = E + S + D + I, N + sum(len(x) for x, _ in both)
                    calls += [(p["status"] != "none", q["status"] != "none")
                              for p, q in zip(human[c][whos[0]], human[c][who])]
            lines.append(f"Clips with two or more transcribers: {len(pairs)}. WER of one against the "
                         f"other: **{pct(E, N):.1f}%**. They agree on whether a span has speech "
                         f"{100 * sum(x == y for x, y in calls) / max(len(calls), 1):.0f}% of the time "
                         f"(Cohen's kappa {kappa(calls):.2f}). A Whisper difference smaller than the "
                         f"transcribers' disagreement is hard to call real.")
        else:
            lines.append("No clip has two transcribers yet (analyst_only/double_transcribe.csv lists "
                         "the clips to double up).")

        # ---- LENA as a clue
        tp = fn = fp = tn = 0
        words_missed = words_all = 0
        for c in clips:
            if lena.get(c) is None:
                continue
            per_span, _ = reference(c)
            for sp, ann_, w in zip(spans[c], refs[c], per_span):
                heard = ann_["status"] != "none" or bool((ann_["speech"] + ann_["media"]).strip())
                lena_says = sp["lena_speech_sec"] >= 0.3
                tp += heard and lena_says
                fn += heard and not lena_says
                fp += lena_says and not heard
                tn += not heard and not lena_says
                if w:
                    words_all += len(w)
                    words_missed += len(w) if not lena_says else 0
        lines += ["", "## How far to trust LENA's speech detection", ""]
        if tp + fn + fp + tn:
            lines.append(
                f"Of the spans where transcribers heard speech, LENA flagged "
                f"**{100 * tp / max(tp + fn, 1):.0f}%**; of the spans LENA flagged, transcribers heard "
                f"speech in {100 * tp / max(tp + fp, 1):.0f}%. "
                f"**{100 * words_missed / max(words_all, 1):.0f}%** of reference words are in spans where "
                f"LENA heard no speech: that is the speech a LENA-only gate would throw away "
                f"(lena_gate pads LENA's segments by a second, so it loses somewhat less).")
        else:
            lines.append("No clip has LENA data.")

    # ---- strata
    strata = [k for k in (next(iter(key.values())) or {}) if k not in NOT_STRATA]
    shown = []
    for col in strata:
        values = sorted({key[c].get(col, "") for c in clips} - {""})
        if not 1 < len(values) <= 12:
            continue
        rows_out = []
        for v in values:
            cs = [c for c in clips if key[c].get(col) == v]
            line = [v, len(cs)]
            for run, filt in ((baseline, "raw"), (best["run"], best["filter"])):
                rs = [r for r in groups[(run, filt)] if r["clip"] in cs]
                line.append(n1(pct(sum(r["S"] + r["D"] + r["I"] for r in rs), total(rs, "N"))))
            rows_out.append(line)
        shown.append(f"### By {col}\n\n" + md_table([col, "clips", f"{baseline} raw",
                                                    f"{best['run']} {best['filter']}"], rows_out))
    if shown:
        lines += ["", "## WER by sample group (all clips)", "",
                  "Groups are small; read these as a check for big gaps, not as estimates.", ""]
        lines += [s + "\n" for s in shown]
    if notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in notes]

    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    say(f"[✓] Report: {os.path.join(out, 'report.md')}")
    base_wer = next(s["tune_wer"] for s in summary if s["run"] == baseline and s["filter"] == "raw")
    ascii1 = lambda x: "n/a" if x != x else f"{x:.1f}%"
    say(f"    Best tuning WER: {best['run']} with {best['filter']} at {ascii1(best['tune_wer'])} "
        f"(held out: {ascii1(best['heldout_wer'])}); baseline {baseline} raw: {ascii1(base_wer)}")
    for n in notes:
        say(f"    [!] {n}")


if __name__ == "__main__":
    main()
