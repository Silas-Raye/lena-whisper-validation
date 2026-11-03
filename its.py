#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
its.py — read LENA .its files.

The Its class and find_its() are copied unchanged from combineTranscripts.py
in the lena-whisper repository, so both repositories read .its files the same
way: every time is in seconds from the start of the recording, which is the
same moment in the .wav file and the .its file. Fix bugs in both places.
"""

import bisect
import csv
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:          # Python 3.8 and older
    ZoneInfo = None


class CombineError(Exception):
    """An .its file can't be read or found; the message says why."""


# ============================================================
# HELPERS
# ============================================================
def seconds(text):
    """'PT12.34S', 'P79.00S', or 'PT1H2M3.4S' in an .its file -> seconds."""
    m = re.fullmatch(r"P(?:T)?(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?", text.strip())
    if not m:
        raise ValueError(f"unexpected time in .its file: {text!r}")
    h, mi, s = m.groups()
    return int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)


def utc_time(text):
    """'2020-10-26T11:58:41Z' (or with fractions of a second) -> UTC datetime."""
    m = re.fullmatch(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(\.\d+)?Z?", text.strip())
    if not m:
        raise ValueError(f"unexpected clock time in .its file: {text!r}")
    *parts, frac = m.groups()
    t = datetime(*map(int, parts), tzinfo=timezone.utc)
    return t + timedelta(seconds=float(frac or 0))


def fmt(x, places=2):
    return "" if x is None else f"{x:.{places}f}"


def id_number(participant_id):
    """'1243_029768' -> '1243': the child's ID, before the recorder number."""
    return participant_id.split("_", 1)[0]


class Its:
    """The parts of a LENA .its file that go on the timeline."""

    def __init__(self, path):
        self.path = path
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as e:
            raise CombineError(f"can't read {path} as an .its file ({e})")
        units = root.findall("ProcessingUnit")
        # One <Recording> per stretch the recorder ran. The audio of all of
        # them is one continuous .wav file; the clock jumps between them.
        self.recordings = []
        self.segments = []
        self.blocks = []
        for unit in units:
            for rec in unit.findall("Recording"):
                self.recordings.append((seconds(rec.get("startTime")),
                                        seconds(rec.get("endTime")),
                                        utc_time(rec.get("startClockTime"))))
                for part in rec:                 # <Pause> and <Conversation>
                    conversation = part.get("num") if part.tag == "Conversation" else None
                    for seg in part.findall("Segment"):
                        self.segments.append(self._segment(seg, conversation))
            for section in unit.iter("FiveMinuteSection"):
                self.blocks.append(self._block(section))
        if not self.recordings:
            raise CombineError(f"{os.path.basename(path)} has no <Recording> in it")
        self.segments.sort(key=lambda s: s["start"])
        self.blocks.sort(key=lambda b: b["start"])
        for number, block in enumerate(self.blocks, start=1):
            block["lena_block"] = number
        self.duration = max(end for _, end, _ in self.recordings)
        self._rec_starts = [start for start, _, _ in self.recordings]
        self._seg_starts = [s["start"] for s in self.segments]
        self._block_starts = [b["start"] for b in self.blocks]
        self.zone, self.zone_note = self._time_zone(root)
        recorder = root.find(".//SRDInfo")
        self.serial = recorder.get("SerialNumber") if recorder is not None else None

    @staticmethod
    def _segment(seg, conversation):
        a = seg.attrib
        words = [float(a[k]) for k in ("femaleAdultWordCnt", "maleAdultWordCnt") if k in a]
        return {
            "source": "lena_segment",
            "start": seconds(a["startTime"]),
            "end": seconds(a["endTime"]),
            "speaker": a.get("spkr", ""),
            "lena_conversation": conversation,
            "adult_words": fmt(sum(words)) if words else None,
            "child_utterances": a.get("childUttCnt"),
            "child_utterance_sec": fmt(seconds(a["childUttLen"])) if "childUttLen" in a else None,
            "child_cry_vfx_sec": fmt(seconds(a["childCryVfxLen"])) if "childCryVfxLen" in a else None,
            "average_db": a.get("average_dB"),
            "peak_db": a.get("peak_dB"),
        }

    @staticmethod
    def _block(section):
        a = section.attrib
        return {
            "source": "lena_block",
            "start": seconds(a["startTime"]),
            "end": seconds(a["endTime"]),
            "adult_words": a.get("adultWordCnt"),
            "child_utterances": a.get("childUttCnt"),
            "child_utterance_sec": fmt(seconds(a["childUttLen"])) if "childUttLen" in a else None,
            "child_cry_vfx_sec": fmt(seconds(a["childCryVfxLen"])) if "childCryVfxLen" in a else None,
            "conversational_turns": a.get("turnTaking"),
            "average_db": a.get("average_dB"),
            "peak_db": a.get("peak_dB"),
        }

    @staticmethod
    def _time_zone(root):
        """The local time zone that the .its file records, for wall-clock times."""
        transfer = root.find(".//TransferTime")
        name = transfer.get("TimeZone") if transfer is not None else None
        if name and ZoneInfo is not None:
            try:
                return ZoneInfo(name), name
            except Exception:
                pass
        zone = root.find(".//Audio/TimeZone")
        if zone is not None and zone.get("StandardSecondsOffset"):
            offset = int(zone.get("StandardSecondsOffset"))
            return (timezone(timedelta(seconds=offset)),
                    f"UTC{offset / 3600:+g} (standard time; daylight saving not applied)")
        return timezone.utc, "UTC (the .its file names no time zone)"

    def clock(self, t):
        """Local wall-clock time, as text, of t seconds into the recording."""
        i = max(bisect.bisect_right(self._rec_starts, t) - 1, 0)
        start, _, start_clock = self.recordings[i]
        local = (start_clock + timedelta(seconds=round(t - start, 2))).astimezone(self.zone)
        return f"{local:%Y-%m-%d %H:%M:%S}.{local.microsecond // 10000:02d}"

    def block_at(self, t):
        """The number of the LENA 5-minute block that contains second t."""
        i = bisect.bisect_right(self._block_starts, t) - 1
        if 0 <= i and t < self.blocks[i]["end"] + 1e-6:
            return self.blocks[i]["lena_block"]
        return None

    def speakers_during(self, a, b):
        """LENA's labels between seconds a and b, with seconds, most first."""
        totals = {}
        i = max(bisect.bisect_right(self._seg_starts, a) - 1, 0)
        while i < len(self.segments) and self.segments[i]["start"] < b:
            seg = self.segments[i]
            overlap = min(b, seg["end"]) - max(a, seg["start"])
            if overlap > 0:
                totals[seg["speaker"]] = totals.get(seg["speaker"], 0.0) + overlap
            i += 1
        ranked = sorted(totals.items(), key=lambda kv: -kv[1])
        return "; ".join(f"{label} {sec:.2f}" for label, sec in ranked if sec >= 0.005)


def read_lookup_table(path):
    """[(ExternalReferenceID, ITS_File_Name)] from the lookup table, such as
    its_identifier_file_babies_crb.csv in this folder."""
    if not os.path.isfile(path):
        raise CombineError(f"no lookup table at {path}")
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = {name.strip(): name for name in (reader.fieldnames or [])}
        if "ExternalReferenceID" not in fields or "ITS_File_Name" not in fields:
            raise CombineError(f"{path} has no ExternalReferenceID and ITS_File_Name columns")
        return [((row[fields["ExternalReferenceID"]] or "").strip(),
                 (row[fields["ITS_File_Name"]] or "").strip()) for row in reader]


def find_its(participant_id, its_dir, lookup, timepoint=""):
    """The path of a participant's .its file in its_dir.

    A file named <participant>.its wins. Otherwise the lookup table finds it:
    the row whose ExternalReferenceID starts with the participant's ID number
    (1243_12mo for 1243_029768) and whose ITS_File_Name ends with the same
    recorder number (20211105_123641_029768). The same child can wear the
    same recorder at more than one age; then the row for `timepoint` (12-mo
    matches 1243_12mo) is used, and otherwise you choose (see the error
    message).
    """
    child = id_number(participant_id)
    recorder = participant_id.split("_", 1)[1] if "_" in participant_id else ""
    folders = [its_dir, os.path.join(its_dir, participant_id[:4])]

    def on_disk(name):
        name = name[:-4] if name.lower().endswith(".its") else name
        return next((p for p in (os.path.join(d, name + ".its") for d in folders)
                     if os.path.isfile(p)), None)

    named = on_disk(participant_id)
    if named:
        return named

    candidates = []
    for external_id, its_name in read_lookup_table(lookup):
        m = re.match(r"\d+", external_id)
        name = its_name[:-4] if its_name.lower().endswith(".its") else its_name
        if m and m.group(0) == child and "_".join(name.split("_")[2:]) == recorder:
            if name not in [c[1] for c in candidates]:
                candidates.append((external_id, name))
    if not candidates:
        raise CombineError(f"no row in the lookup table matches {participant_id} "
                           f"(ID {child}, recorder {recorder})")
    found = [(ext, name, on_disk(name)) for ext, name in candidates]
    present = [f for f in found if f[2]]
    if len(present) > 1 and timepoint:
        age = re.escape(f"{child}_{timepoint.replace('-', '')}")
        at_age = [f for f in present if re.match(age + r"(_|$)", f[0])]
        present = at_age or present
    if len(present) == 1:
        return present[0][2]
    if not present:
        names = ", ".join(f"{name}.its ({ext})" for ext, name, _ in found)
        raise CombineError(f"the lookup table names {names}, but it isn't in {its_dir}")
    choices = "\n".join(f"        {ext:<12} {path}" for ext, _, path in present)
    raise CombineError(
        f"{len(present)} .its files match {participant_id}; the same child wore that "
        f"recorder at more than one age:\n{choices}\n"
        f"    Copy the one for this recording to {participant_id}.its in the same "
        f"folder, then run this again:\n"
        f"        cp {present[0][2]} {os.path.join(its_dir, participant_id + '.its')}")


# ============================================================
# COMBINE
