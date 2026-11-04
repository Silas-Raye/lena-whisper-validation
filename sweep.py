#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sweep.py — transcribe a package's clips with one Whisper model and one named
setting, a "run". run_sweep.sh runs it on a GPU; by hand:
    python sweep.py --package <package folder> --model large-v2 --config prod

Each run's transcripts go in analyst_only/whisper/<model>__<config>/:
    V001.json   Whisper's full output, with timing and device
    V001.csv    the transcript laid out on the template's spans, to read
                next to the human transcript
A clip that already has a .json is skipped, so a job that hits its time
limit loses only the clip in progress; run it again to continue.

The settings are in CONFIGS below. Each one changes a few things from "prod",
which is what batchWhisper.py runs today. Any model works with any setting.

Where to transcribe ("gate"): Whisper invents words in long stretches without
speech, so some settings only transcribe where something says there's speech:
    lena      LENA's speech segments (any speaker label, faint ones and TV
              included), padded by GATE_PAD seconds
    vad       Silero VAD at threshold VAD_THRESHOLD (pip install silero-vad)
    lena+vad  anywhere either one hears speech: LENA as a clue, not the
              final word
score.py tells you how much real speech each gate throws away.
"""

import argparse
import glob
import json
import os
import sys
import time

import common as C

# Windows consoles can't print every character (such as the check marks in
# messages); print a substitute rather than stop.
try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass


# What batchWhisper.py runs; everything not listed is openai-whisper's default:
# condition_on_previous_text=True, temperature fallback 0, 0.2, ..., 1.0,
# no_speech_threshold=0.6, logprob_threshold=-1.0, compression_ratio_threshold=2.4.
BASE = dict(language="en", beam_size=5, best_of=5, patience=1.0)
NO_COND = dict(condition_on_previous_text=False)
GREEDY = dict(beam_size=None, best_of=None, patience=None)
HST = dict(word_timestamps=True, hallucination_silence_threshold=2.0)

CONFIGS = {
    # --- what ACCRE runs today
    "prod": {},
    # --- decoding settings
    # Don't prompt each 30-second window with the previous window's text. The
    # usual fix for repetition loops ("Hang on." for four minutes).
    "no_cond": NO_COND,
    # Skip silent stretches of 2+ seconds when a segment looks made up.
    "no_cond_hst2": {**NO_COND, **HST},
    # The silence gate: a window counts as silence when no_speech_prob is over
    # no_speech_threshold and avg_logprob is under logprob_threshold.
    "no_cond_nst4": {**NO_COND, "no_speech_threshold": 0.4},    # skip more
    "no_cond_nst8": {**NO_COND, "no_speech_threshold": 0.8},    # skip less
    "no_cond_lp08": {**NO_COND, "logprob_threshold": -0.8},     # skip more
    # Stricter repetition check, which retries at a higher temperature.
    "no_cond_cr20": {**NO_COND, "compression_ratio_threshold": 2.0},
    # Greedy search instead of beam search: about twice as fast.
    "no_cond_greedy": {**NO_COND, **GREEDY},
    # A prompt in the household register (can help style; can also leak in).
    "no_cond_prompt": {**NO_COND, "initial_prompt":
                       "Okay, sweetie. Uh-huh, mommy's here. Do you want your bottle? Yeah, good job!"},
    # --- where to transcribe (see the docstring)
    "lena_gate": {**NO_COND, "gate": "lena"},
    "vad_gate": {**NO_COND, "gate": "vad"},
    "lena_or_vad": {**NO_COND, "gate": "lena+vad"},
    "lena_or_vad_hst2": {**NO_COND, **HST, "gate": "lena+vad"},
}

GATE_PAD = 1.0        # seconds added before and after each speech region
GATE_MERGE = 2.0      # regions closer than this are transcribed as one
VAD_THRESHOLD = 0.2   # Silero's default, 0.5, misses most far-field speech in LENA audio

_VAD = None


def say(msg):
    print(msg, flush=True)


def vad_regions(audio, duration):
    """Silero VAD's speech regions as [[start, end], ...] seconds."""
    global _VAD
    import torch
    from silero_vad import get_speech_timestamps, load_silero_vad
    if _VAD is None:
        _VAD = load_silero_vad()
    found = get_speech_timestamps(torch.from_numpy(audio), _VAD, sampling_rate=C.RATE,
                                  threshold=VAD_THRESHOLD, return_seconds=True)
    return [[t["start"], t["end"]] for t in found]


def gate_regions(gate, audio, duration, lena_segments):
    """Where to transcribe, as merged [[start, end], ...] seconds, or None for
    the whole clip (a LENA gate on a clip without LENA data)."""
    regions = []
    if "lena" in gate:
        if lena_segments is None and gate == "lena":
            return None
        regions += C.speech_regions(lena_segments or [], duration, merge_gap=0)
    if "vad" in gate:
        regions += vad_regions(audio, duration)
    spans = []
    for start, end in sorted(regions):
        start, end = max(0.0, start - GATE_PAD), min(duration, end + GATE_PAD)
        if spans and start - spans[-1][1] < GATE_MERGE:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    return spans


def model_root(name, model_dir):
    """The folder that has the model's file: Whisper's usual cache (where the
    main pipeline keeps large-v2) or model_dir, where setup.sh puts the rest."""
    import whisper
    fname = os.path.basename(whisper._MODELS[name])
    default = os.path.join(os.getenv("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "whisper")
    for root in (default, model_dir):
        if root and os.path.isfile(os.path.join(root, fname)):
            return root
    return model_dir or default


def spans_csv(res, template_rows):
    """The transcript laid out on the template's spans. A word goes in the
    span that holds its middle (the segment's middle without word times)."""
    texts = [[] for _ in template_rows]
    bounds = [(float(r["start_sec"]), float(r["end_sec"])) for r in template_rows]

    def place(t, text):
        for i, (a, b) in enumerate(bounds):
            if a <= t < b or (i == len(bounds) - 1 and t >= a):
                texts[i].append(text)
                return
        texts[-1 if t >= bounds[-1][0] else 0].append(text)

    for seg in res["segments"]:
        if seg.get("words"):
            for w in seg["words"]:
                place((w["start"] + w["end"]) / 2, w["word"].strip())
        else:
            place((seg["start"] + seg["end"]) / 2, seg["text"].strip())
    return [{"clip": r["clip"], "span": r["span"], "start_sec": r["start_sec"],
             "end_sec": r["end_sec"], "start": r["start"], "end": r["end"],
             "lena_hint": r["lena_hint"], "whisper_text": " ".join(t for t in texts[i] if t)}
            for i, r in enumerate(template_rows)]


# GPU memory each model needs, in GB, with room for decoding.
NEEDS_GB = {"large": 7.0, "turbo": 4.5, "medium": 4.0, "small": 2.0, "base": 1.0, "tiny": 1.0}


def pick_gpu(torch, model):
    """The visible GPU with the most free memory, as "cuda:<n>". Stops with a
    message (and no traceback) if none has room for the model: on the shared
    interactive_gpu nodes, another job's process can hold the GPU SLURM gave us."""
    need = next((gb for k, gb in NEEDS_GB.items() if model.startswith(k)), 7.0)
    free = []
    for i in range(torch.cuda.device_count()):
        f, total = torch.cuda.mem_get_info(i)
        free.append((f / 1e9, i, total / 1e9))
        say(f"    GPU {i} ({torch.cuda.get_device_name(i)}): {f / 1e9:.1f} of {total / 1e9:.1f} GB free")
    best_free, best, _ = max(free)
    if best_free < need:
        say(f"[X] No GPU on this node has the {need:.0f} GB {model} needs: another process is "
            "using it (see nvidia-smi above). Nothing was lost; run submit_sweep.sh again "
            "later, and tell ACCRE support if it keeps happening on the same node.")
        sys.exit(3)
    return f"cuda:{best}"


def main():
    ap = argparse.ArgumentParser(description="Transcribe a package's clips with one run.")
    ap.add_argument("--package", required=True)
    ap.add_argument("--model", default="large-v2", help="any openai-whisper model: large-v2, turbo, small.en, ...")
    ap.add_argument("--config", default="prod", help="one of: " + ", ".join(CONFIGS))
    ap.add_argument("--model-dir", default="", help="where setup.sh downloaded models")
    ap.add_argument("--clips", default="", help="only these clips, comma-separated (default: all)")
    ap.add_argument("--device", default="")
    ap.add_argument("--threads", type=int, default=0, help="CPU threads (CPU runs)")
    a = ap.parse_args()

    if a.config not in CONFIGS:
        sys.exit(f"[X] No setting called {a.config}. The settings are: {', '.join(CONFIGS)}")
    import torch
    import whisper
    if a.threads:
        torch.set_num_threads(a.threads)
    device = a.device or ("cuda" if torch.cuda.is_available() else "cpu")
    if a.model not in whisper._MODELS:
        sys.exit(f"[X] openai-whisper has no model called {a.model}. "
                 f"It has: {', '.join(whisper.available_models())}")

    trans = os.path.join(a.package, "for_transcribers")
    analyst = os.path.join(a.package, "analyst_only")
    wavs = sorted(glob.glob(os.path.join(trans, "audio", "*.wav")))
    if a.clips:
        keep = set(a.clips.split(","))
        wavs = [w for w in wavs if os.path.basename(w)[:-4] in keep]
    if not wavs:
        sys.exit(f"[X] No clips in {os.path.join(trans, 'audio')}. Run make_package.sh first.")
    run = f"{a.model}__{a.config}"
    out_dir = os.path.join(analyst, "whisper", run)
    os.makedirs(out_dir, exist_ok=True)
    todo = [w for w in wavs if not os.path.isfile(os.path.join(out_dir, os.path.basename(w)[:-4] + ".json"))]
    say(f"[*] Run {run}: {len(wavs) - len(todo)} of {len(wavs)} clips done, {len(todo)} to go")
    if not todo:
        return

    opts = {**BASE, **CONFIGS[a.config], "fp16": device == "cuda"}
    opts = {k: v for k, v in opts.items() if v is not None}
    gate = opts.pop("gate", None)
    if device == "cuda":
        device = pick_gpu(torch, a.model)
    gpu = torch.cuda.get_device_name(device) if device.startswith("cuda") else "cpu"
    t_load = time.time()
    model = whisper.load_model(a.model, device=device, download_root=model_root(a.model, a.model_dir))
    say(f"[✓] {a.model} loaded on {gpu} in {time.time() - t_load:.0f} s")

    for i, wav in enumerate(todo, start=1):
        clip = os.path.basename(wav)[:-4]
        audio = C.load_audio(wav)
        duration = len(audio) / C.RATE
        t0 = time.time()
        regions = None
        if gate:
            lena = C.read_lena(os.path.join(analyst, "lena", clip + ".csv"))
            regions = gate_regions(gate, audio, duration, lena)
        torch.manual_seed(0)   # the temperature fallback samples; keep runs repeatable
        if regions is None:
            res = model.transcribe(audio, verbose=None, **opts)
        elif not regions:
            res = {"text": "", "segments": [], "language": "en"}
        else:
            res = model.transcribe(audio, verbose=None,
                                   clip_timestamps=[t for r in regions for t in r], **opts)
        res["elapsed_s"] = time.time() - t0
        res["duration_s"] = duration
        # A LENA gate on a clip without LENA data transcribes all of it.
        gate_sec = duration if regions is None else sum(e - s for s, e in regions)
        res["run"] = {"model": a.model, "config": a.config, "device": gpu,
                      "options": opts, "gate": gate,
                      "gate_regions": regions, "gate_sec": gate_sec}
        dst = os.path.join(out_dir, clip + ".json")
        with open(dst + ".partial", "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
        os.replace(dst + ".partial", dst)
        template = os.path.join(trans, "templates", clip + ".csv")
        if os.path.isfile(template):
            C.write_csv(os.path.join(out_dir, clip + ".csv"),
                        C.TEMPLATE_COLUMNS[:7] + ["whisper_text"],
                        spans_csv(res, C.read_csv(template)), excel=True)
        say(f"  [✓] {clip}  {i}/{len(todo)}  {res['elapsed_s']:5.0f} s  "
            f"{len(res['segments'])} segments"
            + (f", transcribed {gate_sec:.0f} of {duration:.0f} s" if gate else "")
            + (" (no LENA data)" if gate and regions is None else ""))
    say(f"[✓] Run {run} is done.")


if __name__ == "__main__":
    main()
