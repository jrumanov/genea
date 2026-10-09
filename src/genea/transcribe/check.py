"""Find transcripts that lost part of the recording: Whisper looped, or stopped early."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from genea.index.indexer import scan_files
from genea.ingest.docs import read_raw_transcript
from genea.ingest.transcript import DEAD_SPAN_SECONDS, Segment, dead_spans, format_time
from genea.kb import KnowledgeBase
from genea.transcribe.audio import audio_duration, find_audio


@dataclass(frozen=True)
class LostSpan:
    kind: str  # "dead" (a loop), "early_end" (the transcript stops) or "from" (chosen with --from)
    start: float  # where the lost part begins in the recording
    end: float | None  # None: to the end of the audio
    text: str  # the repeated line of a dead span
    first: int  # index of the first lost segment in the raw segment list
    last: int  # index of the last one


@dataclass(frozen=True)
class CheckResult:
    path: Path
    spans: list[LostSpan]
    audio_seconds: float | None
    speech_end: float  # where real text ends


def find_lost_spans(segments: list[Segment], audio_seconds: float | None) -> list[LostSpan]:
    spans = []
    loop_to_end = False
    for dead in dead_spans(segments):
        to_end = dead.last == len(segments) - 1
        loop_to_end = loop_to_end or to_end
        spans.append(LostSpan("dead", dead.start, None if to_end else dead.end, dead.text, dead.first, dead.last))
    if audio_seconds is not None and segments and not loop_to_end:
        last = segments[-1]
        if audio_seconds - last.end >= DEAD_SPAN_SECONDS:
            spans.append(LostSpan("early_end", last.end, None, "", len(segments) - 1, len(segments) - 1))
    return sorted(spans, key=lambda span: span.first)


def span_range(span: LostSpan) -> str:
    return f"{format_time(span.start)}–{'end' if span.end is None else format_time(span.end)}"


def describe_span(span: LostSpan) -> str:
    if span.kind == "dead":
        return f'dead span {span_range(span)} ({span.last - span.first + 1}× "{span.text}")'
    if span.kind == "early_end":
        return f"transcript ends early: nothing after {format_time(span.start)}"
    return f"from {format_time(span.start)}"


def check_file(path: Path, duration_fn: Callable[[Path], float | None] = audio_duration,
               find_audio_fn: Callable[[Path], Path | None] = find_audio,
               segments: list[Segment] | None = None) -> CheckResult | None:
    """None when the file is not a Whisper transcript. Pass `segments` when the file was already read."""
    if segments is None:
        segments = read_raw_transcript(path)
    if not segments:
        return None
    audio = find_audio_fn(path)
    audio_seconds = duration_fn(audio) if audio else None
    spans = find_lost_spans(segments, audio_seconds)
    return CheckResult(path, spans, audio_seconds, spans[0].start if spans else segments[-1].end)


def transcripts(kb: KnowledgeBase) -> list[tuple[Path, list[Segment]]]:
    """Every transcript of the knowledge base with its raw segments; each file is read once."""
    found = []
    for path, kind in scan_files(kb):
        if kind == "doc" and path.suffix.lower() == ".txt" and (segments := read_raw_transcript(path)):
            found.append((path, segments))
    return found


def transcript_paths(kb: KnowledgeBase) -> list[Path]:
    return [path for path, _ in transcripts(kb)]


def loop_summary(kb: KnowledgeBase) -> str | None:
    found = transcripts(kb)
    if not found:
        return None
    bad = sum(1 for path, segments in found if (result := check_file(path, segments=segments)) and result.spans)
    if bad:
        return f"Transcripts: {bad} of {len(found)} have lost spans; run `genea transcripts check`."
    return f"Transcripts: {len(found)} checked, no lost spans."
