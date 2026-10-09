"""Whisper transcripts: `start<TAB>end<TAB> text` lines, parsed into timed segments."""

from __future__ import annotations

import re
from dataclasses import dataclass

LINE = re.compile(r"^\s*(\d+(?:\.\d+)?)\t(\d+(?:\.\d+)?)\t(.*)$")
DETECT_LINES = 50  # the first non-empty lines decide whether a file is a transcript
DETECT_SHARE = 0.8
LOOP_RUN = 3  # this many identical segments in a row are Whisper looping on silence or noise
FILENAME_DATE = re.compile(r"^(\d{4})_(\d{2})_(\d{2})-(.+)$")


@dataclass
class Segment:
    start: float
    end: float
    text: str


def is_transcript(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()][:DETECT_LINES]
    if not lines:
        return False
    return sum(1 for line in lines if LINE.match(line)) / len(lines) >= DETECT_SHARE


def parse_transcript(text: str) -> list[Segment]:
    """Segments of a Whisper transcript; [] when the text is not one."""
    if not is_transcript(text):
        return []
    segments: list[Segment] = []
    for line in text.splitlines():
        match = LINE.match(line)
        body = match.group(3).strip() if match else ""
        if body:
            segments.append(Segment(float(match.group(1)), float(match.group(2)), body))
    collapsed: list[Segment] = []
    i = 0
    while i < len(segments):
        j = i
        while j + 1 < len(segments) and segments[j + 1].text == segments[i].text:
            j += 1
        if j - i + 1 >= LOOP_RUN:
            collapsed.append(Segment(segments[i].start, segments[j].end, segments[i].text))
        else:
            collapsed.extend(segments[i:j + 1])
        i = j + 1
    return collapsed


def format_time(seconds: float) -> str:
    """m:ss, or h:mm:ss from one hour on."""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def transcript_header(stem: str) -> str:
    """Searchable context from the file name only. The speakers are not split out: whether
    `fabik_lubica` is surname+given or given+surname cannot be told from the name."""
    match = FILENAME_DATE.match(stem)
    if match:
        year, month, day, rest = match.groups()
        return f"Nahrávka {year}-{month}-{day} · {rest.replace('_', ' ')}"
    return f"Nahrávka · {stem.replace('_', ' ')}"
