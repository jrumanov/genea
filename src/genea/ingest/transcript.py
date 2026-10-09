"""Whisper transcripts: `start<TAB>end<TAB> text` lines, parsed into timed segments."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Whisper writes decimal seconds in both columns; "1845<TAB>12<TAB>entry" tables must not match
LINE = re.compile(r"^\s*(\d+\.\d+)\t(\d+\.\d+)\t(.*)$")
DETECT_LINES = 50  # the first non-empty lines decide whether a file is a transcript
DETECT_SHARE = 0.8
LOOP_RUN = 3  # this many identical segments in a row are Whisper looping on silence or noise
DEAD_SPAN_SECONDS = 60.0  # a run of identical segments this long is lost speech, not a stutter
FILENAME_DATE = re.compile(r"^(\d{4})_(\d{2})_(\d{2})-(.+)$")


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Run:
    first: int  # index of the first segment of the run
    last: int  # index of the last one
    text: str


@dataclass(frozen=True)
class DeadSpan:
    start: float
    end: float
    text: str
    repeats: int
    first: int
    last: int


def _match(line: str) -> re.Match | None:
    match = LINE.match(line)
    return match if match and float(match.group(2)) >= float(match.group(1)) else None


def is_transcript(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()][:DETECT_LINES]
    if not lines:
        return False
    return sum(1 for line in lines if _match(line)) / len(lines) >= DETECT_SHARE


def parse_line(line: str) -> Segment | None:
    """The segment on one transcript line; None for stray lines and empty text."""
    match = _match(line)
    body = match.group(3).strip() if match else ""
    return Segment(float(match.group(1)), float(match.group(2)), body) if body else None


def parse_segments(text: str) -> list[Segment]:
    """Segments exactly as written (loops included); [] when the text is not a transcript."""
    if not is_transcript(text):
        return []
    return [segment for line in text.splitlines() if (segment := parse_line(line))]


def find_runs(segments: list[Segment], min_repeats: int = LOOP_RUN) -> list[Run]:
    """Runs of at least `min_repeats` identical consecutive segments."""
    runs: list[Run] = []
    i = 0
    while i < len(segments):
        j = i
        while j + 1 < len(segments) and segments[j + 1].text == segments[i].text:
            j += 1
        if j - i + 1 >= min_repeats:
            runs.append(Run(i, j, segments[i].text))
        i = j + 1
    return runs


def collapse_loops(segments: list[Segment]) -> list[Segment]:
    """Every run of identical segments becomes one segment spanning the run."""
    runs = {run.first: run for run in find_runs(segments)}
    collapsed: list[Segment] = []
    i = 0
    while i < len(segments):
        run = runs.get(i)
        if run:
            collapsed.append(Segment(segments[run.first].start, segments[run.last].end, run.text))
            i = run.last + 1
        else:
            collapsed.append(segments[i])
            i += 1
    return collapsed


def parse_transcript(text: str) -> list[Segment]:
    """Segments of a Whisper transcript; [] when the text is not one."""
    return collapse_loops(parse_segments(text))


def dead_spans(segments: list[Segment], min_seconds: float = DEAD_SPAN_SECONDS) -> list[DeadSpan]:
    """Loops long enough to be lost speech. Shorter runs are repetition in speech."""
    spans: list[DeadSpan] = []
    for run in find_runs(segments):
        start, end = segments[run.first].start, segments[run.last].end
        if end - start >= min_seconds:
            spans.append(DeadSpan(start, end, run.text, run.last - run.first + 1, run.first, run.last))
    return spans


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
