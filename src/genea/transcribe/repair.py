"""Re-transcribe the lost spans of a transcript and splice the result in."""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from genea.ingest.transcript import Segment, dead_spans, parse_line, parse_segments
from genea.transcribe.audio import AUDIO_EXTS, SAMPLE_RATE, audio_duration, find_audio, load_span
from genea.transcribe.check import LostSpan, find_lost_spans
from genea.transcribe.engine import Transcriber
from genea.transcribe.progress import Progress, summary_line


class RepairError(RuntimeError):
    """The transcript cannot be repaired (no audio, not a transcript, not UTF-8)."""


class SpliceError(RuntimeError):
    """The splice would damage the transcript; nothing is written."""


@dataclass(frozen=True)
class Replacement:
    first_line: int  # inclusive line indexes into text.splitlines(keepends=True)
    last_line: int
    segments: list[Segment]


@dataclass
class RepairReport:
    replaced: list[tuple[LostSpan, int]] = field(default_factory=list)  # span, number of new segments
    unreadable: list[tuple[LostSpan, float, float]] = field(default_factory=list)  # span, loop start, loop end
    backup_created: bool = False
    work_file: Path | None = None
    spliced: bool = False
    nothing_to_do: bool = False


def format_segment(segment: Segment) -> str:
    return f"{segment.start:.6f}\t{segment.end:.6f}\t {segment.text}"


def splice(text: str, replacements: list[Replacement]) -> str:
    """Replace line ranges; every other line stays byte-identical."""
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    ordered = sorted(replacements, key=lambda r: r.first_line)
    if not ordered:
        return text
    for before, after in zip(ordered, ordered[1:]):
        if after.first_line <= before.last_line:
            raise SpliceError("two replaced ranges overlap")
    for r in reversed(ordered):
        if not 0 <= r.first_line <= r.last_line < len(lines):
            raise SpliceError(f"lines {r.first_line}-{r.last_line} are outside the transcript")
        last = lines[r.last_line]
        ending = last[len(last.rstrip("\r\n")):]  # the last replaced line's own ending, possibly none
        block = [format_segment(s) + newline for s in r.segments]
        if block:
            block[-1] = block[-1][:-len(newline)] + ending
        lines[r.first_line:r.last_line + 1] = block
    result = "".join(lines)
    original = text.splitlines(keepends=True)
    head = "".join(original[:ordered[0].first_line])
    tail = "".join(original[ordered[-1].last_line + 1:])
    if not result.startswith(head) or not result.endswith(tail) or len(result) < len(head) + len(tail):
        raise SpliceError("the lines around the replaced part would change")
    # Line by line, not parse_segments: that returns [] when the result no longer looks like a transcript.
    starts = [s.start for line in result.splitlines() if (s := parse_line(line))]
    if not starts or starts != sorted(starts):
        raise SpliceError("the timestamps would go out of order")
    return result


def _spans(segments: list[Segment], audio_seconds: float | None, from_s: float | None) -> list[LostSpan]:
    if from_s is None:
        return find_lost_spans(segments, audio_seconds)
    first = next((i for i, s in enumerate(segments) if s.start >= from_s), len(segments))
    return [LostSpan("from", from_s, None, "", first, len(segments) - 1)]


@dataclass(frozen=True)
class _Cut:
    """One piece of audio to re-transcribe and the transcript lines it replaces."""
    span: LostSpan  # the lost span, or several back-to-back ones merged into one
    cut_start: float
    first_line: int
    last_line: int


def _cuts(spans: list[LostSpan], segments: list[Segment], seg_lines: list[int]) -> list[_Cut]:
    """A cut per lost span; spans whose line ranges touch or overlap become one cut, so no audio is
    transcribed twice and the splice never sees overlapping ranges. A cut replaces lines up to the
    span's last segment only: blank or stray lines after the final segment are kept, even when the
    span runs to the end of the audio."""
    cuts: list[_Cut] = []
    for span in spans:
        # Start one segment earlier so a sentence is not cut in half. An early end's `first` is already
        # the last segment written, the one just before the lost part.
        before = span.first if span.kind == "early_end" else span.first - 1
        cut = _Cut(span, 0.0 if before < 0 else segments[before].start, 0 if before < 0 else seg_lines[before],
                   seg_lines[span.last])
        if cuts and cut.first_line <= cuts[-1].last_line + 1:
            prev = cuts.pop()
            end = None if prev.span.end is None or span.end is None else max(prev.span.end, span.end)
            merged = LostSpan(prev.span.kind if prev.span.kind == span.kind else "dead", prev.span.start, end,
                              prev.span.text, prev.span.first, max(prev.span.last, span.last))
            cut = _Cut(merged, prev.cut_start, prev.first_line, max(prev.last_line, cut.last_line))
        cuts.append(cut)
    return cuts


def _write(path: Path, result: str) -> None:
    """Replace the transcript in one step; on failure it stays as it was and no temp file is left."""
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_bytes(result.encode("utf-8"))
        os.replace(temp, path)
    except OSError as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise RepairError(f"Could not write {path.name} (it is unchanged): {exc}") from exc


def repair_transcript(path: Path, work_dir: Path, *, transcriber: Transcriber, language: str,
                      say: Callable[[str], None], from_s: float | None = None, splice_result: bool = True,
                      find_audio_fn: Callable[[Path], Path | None] = find_audio,
                      duration_fn: Callable[[Path], float | None] = audio_duration,
                      load_fn: Callable = load_span) -> RepairReport:
    path = Path(path)
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RepairError(f"{path.name} is not UTF-8 text") from exc
    segments = parse_segments(text)
    if not segments:
        raise RepairError(f"{path.name} is not a Whisper transcript")
    audio = find_audio_fn(path)
    if audio is None:
        raise RepairError(f"No audio file next to {path.name} (looked for {', '.join(AUDIO_EXTS)})")
    report = RepairReport()
    audio_seconds = None if from_s is not None else duration_fn(audio)
    spans = _spans(segments, audio_seconds, from_s)
    if not spans and from_s is None and audio_seconds is None:
        raise RepairError(f"Could not read the length of {audio.name}, so an early end of the transcript could "
                          f"not be checked. If audio support is missing, run `uv sync --extra transcribe`; "
                          f"or pass --from to re-transcribe from a given second.")
    if not spans:
        report.nothing_to_do = True
        return report

    if splice_result:
        backup = path.with_name(path.name + ".orig")
        if not backup.exists():
            try:
                shutil.copy2(path, backup)
            except OSError as exc:
                raise RepairError(f"Could not save the original as {backup.name}: {exc}") from exc
            report.backup_created = True
            say(f"Saved the original as {backup.name}")

    all_lines = text.splitlines(keepends=True)
    seg_lines = [i for i, line in enumerate(all_lines) if parse_line(line)]
    newline = "\r\n" if "\r\n" in text else "\n"
    work_dir.mkdir(parents=True, exist_ok=True)
    report.work_file = work_dir / f"{path.stem}.retranscribe.txt"
    started = time.monotonic()
    written = 0
    replacements: list[Replacement] = []
    with report.work_file.open("wb") as work:
        for cut in _cuts(spans, segments, seg_lines):
            span, cut_start = cut.span, cut.cut_start
            samples = load_fn(audio, cut_start, span.end)
            progress = Progress(len(samples) / SAMPLE_RATE, cut_start)
            new: list[Segment] = []

            def on_segment(s: Segment, new=new, progress=progress, cut_start=cut_start) -> None:
                shifted = Segment(s.start + cut_start, s.end + cut_start, s.text)
                new.append(shifted)
                work.write((format_segment(shifted) + newline).encode("utf-8"))
                work.flush()
                say(progress.line(s.end, s.text))

            transcriber(samples, language, on_segment)
            written += len(new)
            loops = dead_spans(new)
            if not new:  # nothing came back: report the lost span itself (a tail span runs to the audio's end)
                span_end = span.end if span.end is not None else cut_start + len(samples) / SAMPLE_RATE
                report.unreadable.append((span, span.start, span_end))
            elif loops:
                report.unreadable.append((span, loops[0].start, loops[0].end))
            else:
                replacements.append(Replacement(cut.first_line, cut.last_line, new))
                report.replaced.append((span, len(new)))
    say(summary_line(written, time.monotonic() - started, report.work_file))

    if splice_result and replacements:
        _write(path, splice(text, replacements))
        report.spliced = True
    return report
