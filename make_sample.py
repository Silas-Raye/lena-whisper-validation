#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_sample.py — choose 5-minute clips from children's full recordings and
write them to a package's sample.csv.

make_sample.sh runs this with the paths in config.sh. By hand:
    python make_sample.py --out sample.csv --raw-dir DIR --its-dir DIR \\
        [--lookup CSV] [--timepoint 6-mo] 1253 1254 1256

For each child it reads every recording of theirs in --raw-dir (named
<ID>_<recorder>.wav, as the main pipeline copies them) with its .its file,
and picks LENA 5-minute blocks:
  * Only whole blocks (5 minutes) that start between 6:00 and 22:00 local
    time, and never two neighboring blocks of the same recording.
  * --speech blocks with adult words (LENA's AWC above 0), split evenly
    over the child's lowest, middle, and highest thirds of AWC, and spread
    over the recordings (days) and times of day.
  * --quiet blocks with no adult words, to measure how much Whisper makes up
    when nobody is talking.
The choice is random but repeatable (--seed). Children already in sample.csv
keep their clips, so running it again with more IDs only adds the new ones.

The clips line up with the main pipeline's chunks, which also follow the
.its file's blocks. Each clip is named <recording>_block<number>, where the
number counts the recording's blocks from 1, as lena_block does in the main
pipeline's combined CSV.
"""

import argparse
import glob
import os
import random
import re
import sys

import common as C
from its import CombineError, Its, find_its, read_lookup_table

# Windows consoles can't print every character (such as the check marks in
# messages); print a substitute rather than stop.
try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass

# The validation sample's times of day (lena_transcription_validation_sampling).
BANDS = [("morning", 6, 9), ("midday", 10, 13), ("afternoon", 14, 17), ("evening", 18, 21)]
COLUMNS = ["clip_id", "recording_id", "start_sec", "duration_sec", "its_file", "participant",
           "timepoint", "lena_block", "clock_start", "time_band", "awc_count", "awc_level"]


def say(msg):
    print(msg, flush=True)


def time_band(hour):
    return next((name for name, a, b in BANDS if a <= hour <= b), "")


def recordings_of(pid, raw_dir):
    """{recording ID: .wav path} for a child ID (1253) or one recording (1253_029802)."""
    if "_" in pid:
        names = [pid]
    else:
        paths = glob.glob(os.path.join(raw_dir, pid[:4], pid + "_*.wav")) \
            + glob.glob(os.path.join(raw_dir, pid + "_*.wav"))
        names = sorted({os.path.basename(p)[:-4] for p in paths})
    out = {}
    for rec in names:
        for path in (os.path.join(raw_dir, rec[:4], rec + ".wav"), os.path.join(raw_dir, rec + ".wav")):
            if os.path.isfile(path):
                out[rec] = path
                break
    return out


def recorders_at(child, timepoint, lookup):
    """The recorder numbers the lookup table lists for a child at an age, or None."""
    if not lookup or not os.path.isfile(lookup) or not timepoint:
        return None
    age = timepoint.replace("-", "")
    found = set()
    for external_id, its_name in read_lookup_table(lookup):
        if re.match(re.escape(f"{child}_{age}") + r"(_|$)", external_id):
            serial = its_name[:-4].split("_")[-1] if its_name.lower().endswith(".its") \
                else its_name.split("_")[-1]
            if serial.isdigit():
                found.add(int(serial))
    return found or None


def its_for(rec, its_dir, lookup, timepoint):
    """The recording's .its file: <recording>.its, or the name the main
    pipeline's lookup table gives it."""
    if lookup and os.path.isfile(lookup):
        return find_its(rec, its_dir, lookup, timepoint)
    path = os.path.join(its_dir, rec + ".its")
    if os.path.isfile(path):
        return path
    raise CombineError(f"no {rec}.its in {its_dir}, and no lookup table to find its real name")


def spread_pick(blocks, n, rng, taken):
    """Up to n blocks, taking turns over (recording, time of day) groups, and
    never next to a block already taken."""
    groups = {}
    for b in blocks:
        groups.setdefault((b["recording_id"], b["time_band"]), []).append(b)
    for g in groups.values():
        rng.shuffle(g)
    order = sorted(groups)
    rng.shuffle(order)
    picked = []

    def clear(b):
        return all(t["recording_id"] != b["recording_id"] or abs(t["lena_block"] - b["lena_block"]) > 1
                   for t in taken + picked)

    while len(picked) < n and any(groups.values()):
        for key in order:
            if len(picked) >= n:
                break
            while groups[key]:
                b = groups[key].pop()
                if clear(b):
                    picked.append(b)
                    break
    return picked


def main():
    ap = argparse.ArgumentParser(description="Choose clips from full recordings.")
    ap.add_argument("ids", nargs="+", help="child IDs (1253) or recording IDs (1253_029802)")
    ap.add_argument("--out", required=True, help="the package's sample.csv (added to if it exists)")
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--its-dir", required=True)
    ap.add_argument("--lookup", default="")
    ap.add_argument("--timepoint", default="", help="such as 6-mo: only that age's recordings")
    ap.add_argument("--speech", type=int, default=10, help="blocks with adult words per child")
    ap.add_argument("--quiet", type=int, default=2, help="blocks without adult words per child")
    ap.add_argument("--seed", type=int, default=2026)
    a = ap.parse_args()

    existing = C.read_csv(a.out) if os.path.isfile(a.out) else []
    have = {r.get("participant") or r["recording_id"].split("_")[0] for r in existing}
    new_rows = []
    for pid in a.ids:
        child = pid.split("_")[0]
        if child in have:
            say(f"[~] {child}: already in {os.path.basename(a.out)}; its clips stay as they are")
            continue
        rng = random.Random(f"{a.seed}-{child}")
        recs = recordings_of(pid, a.raw_dir)
        allowed = recorders_at(child, a.timepoint, a.lookup) if "_" not in pid else None
        if allowed is not None:
            skipped = [r for r in recs if int(r.split("_")[1]) not in allowed]
            for r in skipped:
                say(f"    {r}: not a {a.timepoint} recording in the lookup table; skipped")
                del recs[r]
        if not recs:
            say(f"[!] {child}: no recordings in {a.raw_dir}. Copy them with the main pipeline's "
                "print_wav_commands.sh.")
            continue

        blocks, its_found = [], False
        for rec, wav in sorted(recs.items()):
            try:
                its_path = its_for(rec, a.its_dir, a.lookup, a.timepoint)
                its = Its(its_path)
            except (CombineError, OSError, ValueError) as e:
                say(f"[!] {rec}: no usable .its file ({e}). Copy it with the main pipeline's "
                    "print_its_commands.sh.")
                continue
            its_found = True
            wav_sec = C.wav_info(wav)[3]
            if abs(wav_sec - its.duration) > 5:
                say(f"    [~] {rec}: the .wav is {wav_sec:.0f} s and {os.path.basename(its_path)} "
                    f"covers {its.duration:.0f} s; using blocks inside both")
            for b in its.blocks:
                if b["end"] - b["start"] < 299 or b["end"] > wav_sec + 0.5:
                    continue
                clock = its.clock(b["start"])
                band = time_band(int(clock[11:13]))
                if not band:
                    continue
                blocks.append({
                    "clip_id": f"{rec}_block{b['lena_block']:03d}", "recording_id": rec,
                    "start_sec": f"{b['start']:.2f}", "duration_sec": f"{b['end'] - b['start']:.2f}",
                    "its_file": os.path.basename(its_path)[:-4], "participant": child,
                    "timepoint": a.timepoint.replace("-", ""), "lena_block": b["lena_block"],
                    "clock_start": clock[:16], "time_band": band,
                    "awc_count": float(b.get("adult_words") or 0),
                })
        speech = sorted((b for b in blocks if b["awc_count"] > 0), key=lambda b: b["awc_count"])
        quiet = [b for b in blocks if b["awc_count"] == 0]
        if not blocks:
            if its_found:
                say(f"[!] {child}: no whole blocks between 6:00 and 22:00")
            continue

        # The child's own thirds of AWC, so "high" means a talkative stretch for that family.
        thirds = [speech[: len(speech) // 3], speech[len(speech) // 3: 2 * len(speech) // 3],
                  speech[2 * len(speech) // 3:]]
        quota = [a.speech // 3] * 3
        for i in range(a.speech % 3):
            quota[2 - i] += 1        # the remainder goes to the talkative thirds first
        chosen = []
        for level, part, n in zip(("low", "medium", "high"), thirds, quota):
            for b in part:
                b["awc_level"] = level
            chosen += spread_pick(part, n, rng, chosen)
        for b in quiet:
            b["awc_level"] = "none"
        chosen += spread_pick(quiet, a.quiet, rng, chosen)
        for b in chosen:
            b["awc_count"] = f"{b['awc_count']:.0f}"
        chosen.sort(key=lambda b: (b["recording_id"], b["lena_block"]))
        new_rows += chosen
        say(f"[✓] {child}: {len(recs)} recording(s), {len(speech)} speech and {len(quiet)} quiet "
            f"blocks to choose from; chose " + ", ".join(
                f"{sum(b['awc_level'] == lv for b in chosen)} {lv}" for lv in ("low", "medium", "high", "none"))
            + " (AWC)")
        if len(chosen) < a.speech + a.quiet:
            say(f"    [~] only {len(chosen)} of the {a.speech + a.quiet} blocks asked for fit the rules")

    if not new_rows:
        say("[~] No new clips; sample.csv is unchanged.")
        return
    columns = list(existing[0].keys()) if existing else []
    columns += [c for c in COLUMNS if c not in columns]
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    C.write_csv(a.out, columns, existing + new_rows)
    say(f"[✓] {len(new_rows)} new clips; {a.out} lists {len(existing) + len(new_rows)}")


if __name__ == "__main__":
    main()
