"""Double-pass transcription: a small model finds the segment boundaries, an accurate model writes the text."""

from __future__ import annotations

import gc
from collections.abc import Callable

from genea.ingest.transcript import Segment, find_runs, format_time
from genea.transcribe.audio import SAMPLE_RATE
from genea.transcribe.engine import Transcriber

MIN_SEGMENT_S = 1.0  # shorter segments are merged into a neighbour: Whisper hallucinates on very short clips
PAD_S = 0.3  # audio added on both sides of a segment, so a word at the edge is not cut
LENGTH_RATIO = 4  # pass 2 text this many times longer or shorter than pass 1 text is flagged
RATIO_MIN_CHARS = 20  # the length comparison needs at least this much pass 1 text


class NoSpeech(RuntimeError):
    """The structure model found no usable segments."""


def _ignore(*_args) -> None:
    pass


def clean_boundaries(segments: list[Segment], min_seconds: float = MIN_SEGMENT_S) -> list[Segment]:
    """Drops empty and looping segments (silence hallucinations), then merges short ones into a neighbour."""
    looped = {i for run in find_runs(segments) for i in range(run.first, run.last + 1)}
    kept = [Segment(s.start, s.end, s.text.strip()) for i, s in enumerate(segments)
            if i not in looped and s.text.strip()]
    merged: list[Segment] = []
    carry: Segment | None = None  # a short segment at the start, waiting for the next one
    for segment in kept:
        if carry:
            segment = Segment(carry.start, segment.end, f"{carry.text} {segment.text}")
            carry = None
        if segment.end - segment.start >= min_seconds:
            merged.append(segment)
        elif merged:
            previous = merged[-1]
            merged[-1] = Segment(previous.start, segment.end, f"{previous.text} {segment.text}")
        else:
            carry = segment
    if carry:
        merged.append(carry)  # the whole recording is shorter than the minimum
    return merged


def _length_mismatch(guide: str, produced: str) -> bool:
    if len(guide) < RATIO_MIN_CHARS:
        return False
    return len(produced) > LENGTH_RATIO * len(guide) or len(produced) * LENGTH_RATIO < len(guide)


def double_pass(samples, language: str, make_structure: Callable[[], Transcriber],
                make_text: Callable[[], Transcriber], on_segment: Callable[[Segment], None], *,
                say: Callable[[str], None] = _ignore, on_structure: Callable[[Segment], None] = _ignore,
                on_text_start: Callable[[], None] = _ignore) -> None:
    """Segments with the boundaries of the structure model and the text of the text model, in time order.
    One model is in memory at a time: the structure model is released before the text model is made."""
    seconds = len(samples) / SAMPLE_RATE
    found: list[Segment] = []

    def collect(segment: Segment) -> None:
        found.append(segment)
        on_structure(segment)

    structure = make_structure()
    structure(samples, language, collect)
    language = language or getattr(structure, "detected_language", "")
    del structure
    gc.collect()
    boundaries = clean_boundaries(found)
    if not boundaries:
        raise NoSpeech("The structure model found no speech; try another --structure-model.")
    say(f"Using {len(boundaries)} of {len(found)} structure segments.")
    if not language:
        say("The language could not be detected; pass --language (or set transcribe_language) "
            "so every segment is not detected on its own.")
    text = make_text()
    on_text_start()
    for guide in boundaries:
        low, high = max(0.0, guide.start - PAD_S), min(seconds, guide.end + PAD_S)
        produced: list[Segment] = []
        text(samples[round(low * SAMPLE_RATE):round(high * SAMPLE_RATE)], language, produced.append)
        when = format_time(guide.start)
        if find_runs(produced):
            say(f"dropped {when}: the text model looped here")
            continue
        joined = " ".join(s.text.strip() for s in produced if s.text.strip())
        if not joined:
            say(f"dropped {when}: the text model found no speech here")
            continue
        if _length_mismatch(guide.text, joined):
            say(f"check: {when} the text length differs a lot from the structure pass; review this segment")
        on_segment(Segment(guide.start, guide.end, joined))
