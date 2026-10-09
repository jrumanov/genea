# Transcribe and Repair Recordings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add CLI commands that transcribe audio with whisper.cpp, find Whisper transcripts that looped or stopped early, and re-transcribe only the lost part and splice it in.

**Architecture:** A new package `genea.transcribe` (progress line, audio loading, whisper.cpp engine, lost-span detection, repair/splice) sits beside the existing `ingest/transcript.py`, which gains one shared run-finding helper so the parser and the checker cannot disagree. Detection reads transcript files at command time, so nothing in the index changes. Three CLI commands (`genea transcribe`, `genea transcripts check`, `genea transcripts repair`) wire it together; MCP is untouched. Heavy parts (the model, audio decoding) are injected, so tests run with fakes and no model.

**Tech Stack:** Python 3.12, Typer, `pywhispercpp` (whisper.cpp) and `av` (audio decoding) as the optional extra `transcribe`, numpy, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-transcribe-repair-design.md`

## Global Constraints

- Default model setting `transcribe_model = "medium"`: an existing file path is used as is, otherwise a model name that `pywhispercpp` downloads on first use. `--model` overrides per run.
- Language: `--language` beats setting `transcribe_language`; empty or `auto` means whisper.cpp auto-detects. Language codes are lower-case (`sk`).
- A **dead span** is at least `LOOP_RUN` (3) identical consecutive segments lasting at least `DEAD_SPAN_SECONDS` (60 s). An **early end** is a transcript whose last segment ends at least 60 s before its audio ends, with no loop. Both are **lost spans**.
- No index change, no schema bump, no KB rebuild. MCP stays read-only and unchanged: nothing in `mcp_server.py` or `tools/` changes.
- `<name>.txt.orig` is never overwritten; `repair` creates it first when it is missing, and stops if that fails.
- Untouched transcript lines stay byte-identical (CRLF/LF and a missing final newline included). The transcript is written through a temp file and `os.replace`.
- Live progress line, printed per decoded segment, exact format: `[ 98.1%] 39:55  elapsed 11.0m  eta 0.2m  To sú kokosové.` (`eta ?` below 1 %).
- Optional extra is named `transcribe`; `check` must work without it; the other commands print `uv sync --extra transcribe` when it is missing.
- All commands print through a UTF-8 stdout (already done by `use_utf8_stdout()` in the app callback; keep it that way and test it).
- Git: work is on branch `jrumanov/transcribe-repair`. Commit after each task; commit messages end with the attribution trailer the session was given. The PR is squashed to one commit and rebased, never merged.

## Review Focus

Failure modes the spec implies but no obvious task test would catch; each is pinned by a named test in the task that owns the code.

- A transcript with LF endings, or without a final newline, must keep exactly those endings after a repair (`test_lf_file_stays_lf`, `test_missing_final_newline_is_kept`, Task 6).
- Running `repair` twice: the second run finds nothing and leaves `.orig` untouched (`test_second_run_changes_nothing`, Task 6).
- A loop shorter than 60 s (a stutter) is never reported or touched (`test_a_short_loop_is_not_a_dead_span`, Task 1; `test_complete_transcript_has_no_lost_spans`, Task 6).
- The model produces nothing, or loops again: the original must not be deleted (`test_empty_output_is_not_spliced`, `test_a_relooping_result_is_not_spliced`, Task 6).
- KB folders with a space in the name (`D:\Foto DB`) and Slovak text on a cp1252 stdout (`test_check_in_a_folder_with_a_space`, `test_check_survives_cp1252_stdout`, Task 7).
- A typo in the model name must list the valid names and not start a download (`test_unknown_model_name_lists_the_valid_ones`, Task 5).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/genea/ingest/transcript.py` (modify) | `parse_line`, `parse_segments` (raw), `find_runs`, `collapse_loops`, `dead_spans`; `parse_transcript` keeps its behaviour |
| `src/genea/ingest/docs.py` (modify) | `read_raw_transcript(path)`: segments with loops not collapsed |
| `src/genea/config.py` (modify) | `transcribe_model`, `transcribe_language` |
| `src/genea/kb.py` (modify) | commented defaults in the generated KB config |
| `src/genea/transcribe/__init__.py` (create) | empty |
| `src/genea/transcribe/progress.py` (create) | the live progress line |
| `src/genea/transcribe/audio.py` (create) | find sibling audio, its duration, cut a span to 16 kHz float32 |
| `src/genea/transcribe/engine.py` (create) | language and model resolution, `WhisperCpp` transcriber |
| `src/genea/transcribe/check.py` (create) | `LostSpan`, `find_lost_spans`, `check_file`, `transcript_paths`, `loop_summary` |
| `src/genea/transcribe/repair.py` (create) | `splice`, `repair_transcript` |
| `src/genea/cli/transcribecmds.py` (create) | the three commands |
| `src/genea/cli/app.py`, `main.py`, `kbcmds.py` (modify) | register `transcripts_app`, import the new module, doctor line |
| `pyproject.toml` (modify) | extra `transcribe`, dev dependencies |
| `README.md` (modify) | usage section and commands table rows |
| `tests/test_transcript.py`, `tests/test_config.py` (modify) | new cases |
| `tests/test_transcribe_progress.py`, `..._audio.py`, `..._engine.py`, `..._check.py`, `..._repair.py`, `tests/test_cli_transcribe.py` (create) | one test file per unit |
| `tests/live/test_live_transcribe.py` (create) | real whisper.cpp, skipped without a model |

Shared test helpers go in `tests/fixtures/transcripts.py` (Task 1).

---

### Task 1: Shared loop helpers in the transcript parser

**Files:**
- Modify: `src/genea/ingest/transcript.py`
- Modify: `src/genea/ingest/docs.py` (add `read_raw_transcript` next to `read_transcript`, line ~127)
- Modify: `tests/fixtures/transcripts.py`
- Test: `tests/test_transcript.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (used by every later task):
  - `DEAD_SPAN_SECONDS = 60.0`
  - `Run(first: int, last: int, text: str)`; `DeadSpan(start: float, end: float, text: str, repeats: int, first: int, last: int)` (frozen dataclasses; `first`/`last` index the raw segment list)
  - `parse_line(line: str) -> Segment | None`
  - `parse_segments(text: str) -> list[Segment]` (raw; `[]` when not a transcript)
  - `find_runs(segments: list[Segment], min_repeats: int = LOOP_RUN) -> list[Run]`
  - `collapse_loops(segments) -> list[Segment]`
  - `dead_spans(segments, min_seconds: float = DEAD_SPAN_SECONDS) -> list[DeadSpan]`
  - `read_raw_transcript(path: Path) -> list[Segment]` in `ingest/docs.py`
  - test helpers `loop_rows(start, count, text=LOOP_TEXT)` and `speech_rows(count)` in `tests/fixtures/transcripts.py`

- [ ] **Step 1: Add the test helpers**

Append to `tests/fixtures/transcripts.py`:

```python
LOOP_TEXT = "No a ja tam si sa učila."


def loop_rows(start: float, count: int, text: str = LOOP_TEXT) -> list[tuple[float, float, str]]:
    """`count` identical one-second segments from `start`: Whisper looping."""
    return [(start + i, start + i + 1, text) for i in range(count)]


def speech_rows(count: int) -> list[tuple[float, float, str]]:
    """`count` different ten-second segments from 0 s."""
    return [(i * 10.0, i * 10.0 + 10.0, f"Veta {i}.") for i in range(count)]
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_transcript.py` (extend the existing imports with `DeadSpan, Run, dead_spans, find_runs, parse_line, parse_segments` and `from fixtures.transcripts import LOOP_TEXT, loop_rows, speech_rows, whisper`):

```python
def test_parse_line_returns_a_segment_or_none():
    assert parse_line("0.600000\t8.560000\t Dobre.\r\n") == Segment(0.6, 8.56, "Dobre.")
    assert parse_line("stray line") is None
    assert parse_line("1.000000\t2.000000\t   \n") is None


def test_parse_segments_keeps_loops_that_parse_transcript_collapses():
    text = whisper([(0, 2, "A."), (2, 4, "B."), (4, 6, "B."), (6, 8, "B."), (8, 10, "C.")])
    assert len(parse_segments(text)) == 5
    assert len(parse_transcript(text)) == 3
    assert parse_segments("Kronika obce Brno.\n") == []


def test_find_runs_needs_three_in_a_row():
    segs = [Segment(0, 1, "a"), Segment(1, 2, "b"), Segment(2, 3, "b"),
            Segment(3, 4, "c"), Segment(4, 5, "c"), Segment(5, 6, "c")]
    assert find_runs(segs) == [Run(3, 5, "c")]


def test_a_long_loop_is_a_dead_span():
    rows = speech_rows(2) + loop_rows(20, 100)
    assert dead_spans(parse_segments(whisper(rows))) == [DeadSpan(20.0, 120.0, LOOP_TEXT, 100, 2, 101)]


def test_a_short_loop_is_not_a_dead_span():
    rows = speech_rows(1) + loop_rows(10, 30)  # 30 s of the same line is a stutter
    assert dead_spans(parse_segments(whisper(rows))) == []
```

- [ ] **Step 3: Run the tests and see them fail**

Run: `uv run pytest tests/test_transcript.py -q`
Expected: FAIL with `ImportError: cannot import name 'DeadSpan'`.

- [ ] **Step 4: Implement**

In `src/genea/ingest/transcript.py`, below `LOOP_RUN = 3 ...` add `DEAD_SPAN_SECONDS = 60.0  # a run of identical segments this long is lost speech, not a stutter`. Below the `Segment` dataclass add:

```python
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
```

Replace the whole `parse_transcript` function with:

```python
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
    spans = []
    for run in find_runs(segments):
        start, end = segments[run.first].start, segments[run.last].end
        if end - start >= min_seconds:
            spans.append(DeadSpan(start, end, run.text, run.last - run.first + 1, run.first, run.last))
    return spans
```

In `src/genea/ingest/docs.py`, change the import to `from genea.ingest.transcript import Segment, parse_segments, parse_transcript` and add after `read_transcript`:

```python
def read_raw_transcript(path: Path) -> list[Segment]:
    """Segments as written, with Whisper loops not collapsed; [] for any other file."""
    return parse_segments(_read_text_file(path)) if path.suffix.lower() == ".txt" else []
```

- [ ] **Step 5: Run the whole transcript test set**

Run: `uv run pytest tests/test_transcript.py tests/test_docs.py tests/test_transcript_indexing.py -q`
Expected: PASS (the parser's behaviour is unchanged, so the old tests still pass).

- [ ] **Step 6: Commit**

```bash
git add src/genea/ingest/transcript.py src/genea/ingest/docs.py tests/fixtures/transcripts.py tests/test_transcript.py
git commit -m "Share the Whisper loop detection between the parser and a dead-span finder"
```

---

### Task 2: Settings and the optional extra

**Files:**
- Modify: `src/genea/config.py:13-26`, `src/genea/kb.py:13-18`, `pyproject.toml`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings.transcribe_model: str = "medium"`, `Settings.transcribe_language: str = ""`; extra `transcribe`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_transcribe_defaults_and_overrides(isolated_home, tmp_path):
    s = load_settings(None)
    assert s.transcribe_model == "medium"
    assert s.transcribe_language == ""  # empty: auto-detect
    kb = tmp_path / "kb"
    (kb / ".genea").mkdir(parents=True)
    (kb / ".genea" / "config.toml").write_text(
        'transcribe_model = "large-v3"\ntranscribe_language = "sk"\n', encoding="utf-8")
    s = load_settings(kb)
    assert (s.transcribe_model, s.transcribe_language) == ("large-v3", "sk")
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run pytest tests/test_config.py::test_transcribe_defaults_and_overrides -q`
Expected: FAIL (`AttributeError: ... 'transcribe_model'`).

- [ ] **Step 3: Implement**

In `Settings` (after `summary_model`) add:

```python
    transcribe_model: str = "medium"  # whisper.cpp model name (downloaded on first use) or path to a ggml .bin
    transcribe_language: str = ""  # language code such as "sk"; empty or "auto": whisper.cpp detects it
```

In `DEFAULT_KB_CONFIG` in `kb.py` append two commented lines:

```
# transcribe_model = "medium"         # or "large-v3", or the path of a ggml .bin file
# transcribe_language = "sk"          # empty: auto-detect
```

In `pyproject.toml` add under `[project.optional-dependencies]`:

```toml
transcribe = ["pywhispercpp>=1.5", "av>=14", "numpy>=2"]
```

and extend the dev group: `dev = ["mcp>=2.3", "pillow>=12.0", "pytest>=8.4", "reportlab>=5.0", "av>=14", "numpy>=2"]`.

- [ ] **Step 4: Sync and run the tests**

Run: `uv sync --extra mcp --extra transcribe` then `uv run pytest tests/test_config.py tests/test_kb.py -q`
Expected: PASS; `uv.lock` has changed.

- [ ] **Step 5: Commit**

```bash
git add src/genea/config.py src/genea/kb.py pyproject.toml uv.lock tests/test_config.py
git commit -m "Add transcribe_model and transcribe_language settings and the transcribe extra"
```

---

### Task 3: The live progress line

**Files:**
- Create: `src/genea/transcribe/__init__.py` (empty), `src/genea/transcribe/progress.py`
- Test: `tests/test_transcribe_progress.py`

**Interfaces:**
- Consumes: `format_time(seconds) -> str` from `genea.ingest.transcript`.
- Produces: `Progress(total_s: float, offset_s: float = 0.0, clock=time.monotonic)` with `.line(end_s: float, text: str) -> str` (end_s is relative to the start of the cut audio; counts the segment), `.count: int`, `.elapsed() -> float`, `.summary(path) -> str`; `summary_line(count: int, seconds: float, path) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
from genea.transcribe.progress import Progress, summary_line


class Clock:
    def __init__(self, *times):
        self.times = list(times)

    def __call__(self):
        return self.times.pop(0)


def test_line_has_the_documented_format():
    progress = Progress(total_s=100.0, offset_s=2297.0, clock=Clock(0.0, 660.0))
    assert progress.line(98.1, "To sú kokosové.") == "[ 98.1%] 39:55  elapsed 11.0m  eta 0.2m  To sú kokosové."


def test_eta_is_unknown_below_one_percent():
    progress = Progress(total_s=100.0, clock=Clock(0.0, 5.0))
    assert progress.line(0.5, "Dobre.") == "[  0.5%] 0:00  elapsed 0.1m  eta ?  Dobre."


def test_time_is_on_the_recordings_timeline_and_percent_is_capped():
    progress = Progress(total_s=10.0, offset_s=3600.0, clock=Clock(0.0, 60.0))
    assert progress.line(12.0, "x").startswith("[100.0%] 1:00:12")


def test_count_and_summary():
    progress = Progress(total_s=10.0, clock=Clock(0.0, 1.0, 2.0, 120.0))
    progress.line(1.0, "a")
    progress.line(2.0, "b")
    assert progress.count == 2
    assert progress.summary("out.txt") == "2 segments written to out.txt in 2.0m"
    assert summary_line(1, 30.0, "x.txt") == "1 segments written to x.txt in 0.5m"
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_transcribe_progress.py -q`
Expected: FAIL (`ModuleNotFoundError: genea.transcribe`).

- [ ] **Step 3: Implement**

`src/genea/transcribe/progress.py`:

```python
"""The live progress line of the transcribe commands: one line per decoded segment."""

from __future__ import annotations

import time
from collections.abc import Callable

from genea.ingest.transcript import format_time


def summary_line(count: int, seconds: float, path) -> str:
    return f"{count} segments written to {path} in {seconds / 60:.1f}m"


class Progress:
    """`total_s` is the length of the audio being transcribed; `offset_s` is where that audio starts in
    the recording, so the time shown matches the transcript file and not the position inside a cut."""

    def __init__(self, total_s: float, offset_s: float = 0.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.total = max(total_s, 0.001)
        self.offset = offset_s
        self._clock = clock
        self._started = clock()
        self.count = 0

    def elapsed(self) -> float:
        return self._clock() - self._started

    def line(self, end_s: float, text: str) -> str:
        self.count += 1
        done = min(max(end_s / self.total, 0.0), 1.0)
        elapsed = self.elapsed()
        eta = "?" if done < 0.01 else f"{(elapsed / done - elapsed) / 60:.1f}m"
        return f"[{done * 100:5.1f}%] {format_time(end_s + self.offset)}  elapsed {elapsed / 60:.1f}m  eta {eta}  {text}"

    def summary(self, path) -> str:
        return summary_line(self.count, self.elapsed(), path)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_transcribe_progress.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/genea/transcribe tests/test_transcribe_progress.py
git commit -m "Add the live progress line for transcription"
```

---

### Task 4: Audio lookup, duration and cutting

**Files:**
- Create: `src/genea/transcribe/audio.py`
- Test: `tests/test_transcribe_audio.py`

**Interfaces:**
- Produces: `AUDIO_EXTS`, `SAMPLE_RATE = 16000`, `class AudioUnavailable(RuntimeError)`, `find_audio(transcript: Path) -> Path | None`, `audio_duration(path: Path) -> float | None` (None without `av` or when unreadable), `load_span(path: Path, start: float = 0.0, end: float | None = None) -> numpy.ndarray` (float32, mono, 16 kHz; raises `AudioUnavailable`).

- [ ] **Step 1: Write the failing tests**

```python
import math
import struct
import wave

import pytest

from genea.transcribe.audio import SAMPLE_RATE, AudioUnavailable, audio_duration, find_audio, load_span


def write_wav(path, seconds):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 20))) for i in range(int(seconds * SAMPLE_RATE))))


def test_find_audio_looks_for_a_sibling_with_the_same_stem(tmp_path):
    transcript = tmp_path / "2023_x.txt"
    transcript.write_text("x", encoding="utf-8")
    assert find_audio(transcript) is None
    (tmp_path / "2023_x.mp3").write_bytes(b"x")
    assert find_audio(transcript) == tmp_path / "2023_x.mp3"
    (tmp_path / "2023_x.m4a").write_bytes(b"x")
    assert find_audio(transcript) == tmp_path / "2023_x.m4a"  # .m4a is searched first


def test_duration_and_span(tmp_path):
    pytest.importorskip("av")
    path = tmp_path / "a.wav"
    write_wav(path, 3.0)
    assert audio_duration(path) == pytest.approx(3.0, abs=0.05)
    whole = load_span(path)
    assert whole.dtype.name == "float32" and abs(len(whole) - 3 * SAMPLE_RATE) <= SAMPLE_RATE // 10
    part = load_span(path, 1.0, 2.0)
    assert abs(len(part) - SAMPLE_RATE) <= SAMPLE_RATE // 10
    tail = load_span(path, 2.0)
    assert abs(len(tail) - SAMPLE_RATE) <= SAMPLE_RATE // 10


def test_unreadable_audio_has_no_duration_and_fails_to_load(tmp_path):
    pytest.importorskip("av")
    bad = tmp_path / "bad.m4a"
    bad.write_bytes(b"not audio")
    assert audio_duration(bad) is None
    with pytest.raises(AudioUnavailable):
        load_span(bad)
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_transcribe_audio.py -q`
Expected: FAIL (`ModuleNotFoundError: genea.transcribe.audio`).

- [ ] **Step 3: Implement**

```python
"""Find the audio that belongs to a transcript and cut parts of it for whisper.cpp."""

from __future__ import annotations

from pathlib import Path

AUDIO_EXTS = (".m4a", ".mp3", ".wav", ".mp4", ".ogg", ".flac")
SAMPLE_RATE = 16000
EXTRA_HINT = "Audio support is not installed: run `uv sync --extra transcribe`."


class AudioUnavailable(RuntimeError):
    """The audio cannot be read (the extra is missing, or the file is damaged)."""


def find_audio(transcript: Path) -> Path | None:
    for ext in AUDIO_EXTS:
        candidate = transcript.with_suffix(ext)
        if candidate.is_file():
            return candidate
    return None


def audio_duration(path: Path) -> float | None:
    """Length in seconds; None without the `av` package or when the file cannot be read."""
    try:
        import av
    except ImportError:
        return None
    try:
        with av.open(str(path)) as container:
            return float(container.duration) / av.time_base if container.duration else None
    except Exception:  # noqa: BLE001 - any decoder failure means "unknown length"
        return None


def load_span(path: Path, start: float = 0.0, end: float | None = None):
    """Mono 16 kHz float32 samples of [start, end) seconds; `end=None` runs to the end of the audio."""
    try:
        import av
        import numpy as np
    except ImportError as exc:
        raise AudioUnavailable(EXTRA_HINT) from exc
    try:
        chunks = []
        first_time = None
        with av.open(str(path)) as container:
            stream = container.streams.audio[0]
            if start > 0:
                container.seek(int(start * av.time_base))
            resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
            for frame in container.decode(stream):
                frame_time = frame.time or 0.0
                if frame_time + frame.samples / frame.sample_rate <= start:
                    continue  # the seek lands before the start; skip whole frames that end before it
                if end is not None and frame_time >= end:
                    break
                if first_time is None:
                    first_time = frame_time
                chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(frame))
            chunks.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
    except AudioUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - av raises many error types
        raise AudioUnavailable(f"Could not read {path.name}: {exc}") from exc
    samples = np.concatenate(chunks).astype("float32") if chunks else np.zeros(0, dtype="float32")
    samples = samples[max(0, round((start - (first_time or start)) * SAMPLE_RATE)):]
    if end is not None:
        samples = samples[:round((end - start) * SAMPLE_RATE)]
    return samples
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_transcribe_audio.py -q`
Expected: PASS. If `test_unreadable_audio...` fails because `av` accepts the file, change the bad file to 2 KB of `os.urandom` bytes and rerun.

- [ ] **Step 5: Commit**

```bash
git add src/genea/transcribe/audio.py tests/test_transcribe_audio.py
git commit -m "Add audio lookup, duration and span cutting for transcription"
```

---

### Task 5: Engine: language, model and the whisper.cpp transcriber

**Files:**
- Create: `src/genea/transcribe/engine.py`
- Test: `tests/test_transcribe_engine.py`

**Interfaces:**
- Consumes: `Segment` from `genea.ingest.transcript`.
- Produces:
  - `Transcriber = Callable[[samples, str, Callable[[Segment], None]], None]`: called as `transcriber(samples, language, on_segment)`; segment times are seconds from the start of `samples`
  - `resolve_language(flag: str | None, setting: str) -> str` (`""` means auto)
  - `language_label(language: str) -> str`
  - `class ModelUnavailable(RuntimeError)`
  - `resolve_model(name: str, notify: Callable[[str], None] = ..., *, constants=None, utils=None) -> Path`
  - `class WhisperCpp(model_path: Path, say: Callable[[str], None] = ...)`, callable as a `Transcriber`

- [ ] **Step 1: Write the failing tests**

```python
from types import SimpleNamespace

import pytest

from genea.transcribe.engine import ModelUnavailable, language_label, resolve_language, resolve_model


@pytest.mark.parametrize(("flag", "setting", "expected"), [
    ("sk", "cs", "sk"), (None, "cs", "cs"), ("", "cs", "cs"), (None, "", ""), ("AUTO", "sk", ""),
    (None, "Auto", ""), (" SK ", "", "sk"),
])
def test_language_resolution(flag, setting, expected):
    assert resolve_language(flag, setting) == expected


def test_language_label():
    assert language_label("") == "auto-detect"
    assert language_label("sk") == "sk"


def fakes(tmp_path, downloaded=True):
    models = tmp_path / "models"
    log = []

    def download_model(name, folder):
        log.append(name)
        if not downloaded:
            return None
        models.mkdir(exist_ok=True)
        target = models / f"ggml-{name}.bin"
        target.write_bytes(b"model")
        return str(target)

    constants = SimpleNamespace(AVAILABLE_MODELS=["tiny", "medium", "large-v3"], MODELS_DIR=models)
    return constants, SimpleNamespace(download_model=download_model), log


def test_an_existing_file_is_used_as_it_is(tmp_path):
    model = tmp_path / "my.bin"
    model.write_bytes(b"x")
    constants, utils, log = fakes(tmp_path)
    assert resolve_model(str(model), constants=constants, utils=utils) == model.resolve()
    assert log == []


def test_a_known_name_is_downloaded_once_with_a_notice(tmp_path):
    constants, utils, log = fakes(tmp_path)
    notes = []
    first = resolve_model("medium", notes.append, constants=constants, utils=utils)
    assert first.name == "ggml-medium.bin" and first.is_file()
    assert len(notes) == 1 and "medium" in notes[0]
    resolve_model("medium", notes.append, constants=constants, utils=utils)
    assert log == ["medium"] and len(notes) == 1  # cached the second time, no notice


def test_unknown_model_name_lists_the_valid_ones(tmp_path):
    constants, utils, log = fakes(tmp_path)
    with pytest.raises(ModelUnavailable) as err:
        resolve_model("mediun", constants=constants, utils=utils)
    assert "tiny, medium, large-v3" in str(err.value)
    assert log == []  # nothing was downloaded


def test_a_failed_download_names_the_model_and_the_folder(tmp_path):
    constants, utils, _ = fakes(tmp_path, downloaded=False)
    with pytest.raises(ModelUnavailable) as err:
        resolve_model("medium", constants=constants, utils=utils)
    assert "medium" in str(err.value) and str(constants.MODELS_DIR) in str(err.value)
    assert "transcribe_model" in str(err.value)
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_transcribe_engine.py -q`
Expected: FAIL (`ModuleNotFoundError: genea.transcribe.engine`).

- [ ] **Step 3: Implement**

```python
"""whisper.cpp through pywhispercpp: language and model resolution, and the transcriber."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from genea.ingest.transcript import Segment

Transcriber = Callable[..., None]  # transcriber(samples, language, on_segment); times are seconds from samples[0]

EXTRA_HINT = "Transcription is not installed: run `uv sync --extra transcribe`."
APPROX_SIZE = {"tiny": "75 MB", "base": "142 MB", "small": "466 MB", "medium": "1.5 GB",
               "large-v1": "2.9 GB", "large-v2": "2.9 GB", "large-v3": "2.9 GB", "large-v3-turbo": "1.6 GB"}


class ModelUnavailable(RuntimeError):
    """The model cannot be found or downloaded, or pywhispercpp is not installed."""


def resolve_language(flag: str | None, setting: str) -> str:
    """--language beats the setting; empty or "auto" means whisper.cpp detects the language ("")."""
    value = (flag or setting or "").strip().lower()
    return "" if value in ("", "auto") else value


def language_label(language: str) -> str:
    return language or "auto-detect"


def _pywhispercpp():
    try:
        from pywhispercpp import constants, utils
    except ImportError as exc:
        raise ModelUnavailable(EXTRA_HINT) from exc
    return constants, utils


def resolve_model(name: str, notify: Callable[[str], None] = lambda message: None, *,
                  constants=None, utils=None) -> Path:
    """A file path is used as it is; a known model name is downloaded on first use and cached."""
    path = Path(name).expanduser()
    if path.is_file():
        return path.resolve()
    if constants is None or utils is None:
        constants, utils = _pywhispercpp()
    if name not in constants.AVAILABLE_MODELS:
        raise ModelUnavailable(
            f"'{name}' is neither a file nor a known model name. Known names: "
            f"{', '.join(constants.AVAILABLE_MODELS)}. Set transcribe_model to the path of a ggml .bin file "
            "to use your own.")
    target = Path(constants.MODELS_DIR) / f"ggml-{name}.bin"
    if not target.is_file():
        notify(f"Downloading model '{name}' ({APPROX_SIZE.get(name, 'size varies')}) to "
               f"{constants.MODELS_DIR}; this happens once.")
        try:
            downloaded = utils.download_model(name, constants.MODELS_DIR)
        except Exception as exc:  # noqa: BLE001 - network and disk errors alike
            raise ModelUnavailable(_download_failed(name, constants, exc)) from exc
        if not downloaded:
            raise ModelUnavailable(_download_failed(name, constants, "no file was written"))
        target = Path(downloaded)
    return target


def _download_failed(name: str, constants, reason) -> str:
    return (f"Could not download model '{name}' to {constants.MODELS_DIR} ({reason}). Download a ggml model "
            "yourself and set transcribe_model to its path (the models of Audacity's OpenVINO plugin work).")


class WhisperCpp:
    """The real transcriber. Loading the model takes a few seconds; one instance is used per run."""

    def __init__(self, model_path: Path, say: Callable[[str], None] = lambda message: None) -> None:
        try:
            from pywhispercpp.model import Model
        except ImportError as exc:
            raise ModelUnavailable(EXTRA_HINT) from exc
        self._say = say
        self._model = Model(str(model_path), n_threads=os.cpu_count() or 4, print_progress=False,
                            print_realtime=False, redirect_whispercpp_logs_to=os.devnull)

    def __call__(self, samples, language: str, on_segment: Callable[[Segment], None]) -> None:
        if not language:
            try:
                detected = self._model.auto_detect_language(samples)[0][0]
                self._say(f"Detected language: {detected}")
            except Exception:  # noqa: BLE001 - detection is informational only
                pass

        def callback(segment) -> None:
            text = segment.text.strip()
            if text:
                on_segment(Segment(segment.t0 / 100, segment.t1 / 100, text))  # whisper.cpp counts in 10 ms

        self._model.transcribe(samples, language=language, no_context=True, new_segment_callback=callback)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_transcribe_engine.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/genea/transcribe/engine.py tests/test_transcribe_engine.py
git commit -m "Add language and model resolution and the whisper.cpp transcriber"
```

---

### Task 6: Lost-span detection and repair

**Files:**
- Create: `src/genea/transcribe/check.py`, `src/genea/transcribe/repair.py`
- Test: `tests/test_transcribe_check.py`, `tests/test_transcribe_repair.py`

**Interfaces:**
- Consumes: Task 1 (`dead_spans`, `parse_segments`, `parse_line`, `read_raw_transcript`, `DEAD_SPAN_SECONDS`), Task 3 (`Progress`, `summary_line`), Task 4 (`find_audio`, `audio_duration`, `load_span`, `SAMPLE_RATE`, `AUDIO_EXTS`), Task 5 (`Transcriber`), `scan_files` from `genea.index.indexer`, `KnowledgeBase`.
- Produces:
  - `check.py`: `LostSpan(kind: str, start: float, end: float | None, text: str, first: int, last: int)` where kind is `"dead"`, `"early_end"` or `"from"` and `end=None` means "to the end of the audio"; `find_lost_spans(segments, audio_seconds) -> list[LostSpan]`; `describe_span(span) -> str`; `CheckResult(path, spans, audio_seconds, speech_end)`; `check_file(path, duration_fn=audio_duration, find_audio_fn=find_audio) -> CheckResult | None`; `transcript_paths(kb) -> list[Path]`; `loop_summary(kb) -> str | None`
  - `repair.py`: `RepairError`, `SpliceError`, `Replacement(first_line, last_line, segments)`, `format_segment(segment) -> str`, `splice(text, replacements) -> str`, `RepairReport`, `repair_transcript(path, work_dir, *, transcriber, language, say, from_s=None, splice_result=True, find_audio_fn=find_audio, duration_fn=audio_duration, load_fn=load_span) -> RepairReport`

- [ ] **Step 1: Write the failing check tests**

`tests/test_transcribe_check.py`:

```python
from fixtures.transcripts import LOOP_TEXT, loop_rows, speech_rows, whisper

from genea.config import Settings
from genea.ingest.transcript import parse_segments
from genea.kb import KnowledgeBase
from genea.transcribe.check import LostSpan, check_file, find_lost_spans, loop_summary, transcript_paths


def segs(rows):
    return parse_segments(whisper(rows))


def test_a_loop_to_the_end_is_a_dead_span_up_to_the_end_of_the_audio():
    spans = find_lost_spans(segs(speech_rows(6) + loop_rows(60, 100)), audio_seconds=160.0)
    assert spans == [LostSpan("dead", 60.0, None, LOOP_TEXT, 6, 105)]


def test_a_loop_in_the_middle_keeps_its_end():
    rows = speech_rows(6) + loop_rows(60, 100) + [(160.0, 170.0, "Potom.")]
    assert find_lost_spans(segs(rows), audio_seconds=170.0) == [LostSpan("dead", 60.0, 160.0, LOOP_TEXT, 6, 105)]


def test_a_transcript_that_stops_early_is_an_early_end():
    assert find_lost_spans(segs(speech_rows(6)), audio_seconds=200.0) == [LostSpan("early_end", 60.0, None, "", 5, 5)]


def test_early_end_needs_the_audio_length():
    assert find_lost_spans(segs(speech_rows(6)), audio_seconds=None) == []


def test_complete_transcript_has_no_lost_spans():
    assert find_lost_spans(segs(speech_rows(6)), audio_seconds=62.0) == []  # 2 s of silence at the end is fine
    short_loop = speech_rows(1) + loop_rows(10, 30) + [(40.0, 50.0, "Hotovo.")]
    assert find_lost_spans(segs(short_loop), audio_seconds=50.0) == []


def test_check_file_reports_where_real_text_ends(tmp_path):
    path = tmp_path / "x.txt"
    path.write_text(whisper(speech_rows(6) + loop_rows(60, 100)), encoding="utf-8")
    (tmp_path / "x.m4a").write_bytes(b"audio")
    result = check_file(path, duration_fn=lambda audio: 160.0)
    assert result.speech_end == 60.0 and result.audio_seconds == 160.0 and len(result.spans) == 1


def test_check_file_without_audio_still_finds_dead_spans(tmp_path):
    path = tmp_path / "x.txt"
    path.write_text(whisper(speech_rows(6) + loop_rows(60, 100)), encoding="utf-8")
    result = check_file(path)
    assert result.audio_seconds is None and len(result.spans) == 1


def test_check_file_ignores_ordinary_text(tmp_path):
    path = tmp_path / "kronika.txt"
    path.write_text("Kronika obce Brno.\n", encoding="utf-8")
    assert check_file(path) is None


def test_transcript_paths_and_summary(tmp_path):
    root = tmp_path.resolve()
    (root / "docs").mkdir()
    (root / "docs" / "good.txt").write_text(whisper(speech_rows(6)), encoding="utf-8")
    (root / "docs" / "bad.txt").write_text(whisper(speech_rows(6) + loop_rows(60, 100)), encoding="utf-8")
    (root / "docs" / "kronika.txt").write_text("Kronika obce Brno.\n", encoding="utf-8")
    kb = KnowledgeBase(root, Settings())
    assert [p.name for p in transcript_paths(kb)] == ["bad.txt", "good.txt"]
    assert "1 of 2" in loop_summary(kb)
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_transcribe_check.py -q`
Expected: FAIL (`ModuleNotFoundError: genea.transcribe.check`).

- [ ] **Step 3: Implement `check.py`**

```python
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
               find_audio_fn: Callable[[Path], Path | None] = find_audio) -> CheckResult | None:
    """None when the file is not a Whisper transcript."""
    segments = read_raw_transcript(path)
    if not segments:
        return None
    audio = find_audio_fn(path)
    audio_seconds = duration_fn(audio) if audio else None
    spans = find_lost_spans(segments, audio_seconds)
    return CheckResult(path, spans, audio_seconds, spans[0].start if spans else segments[-1].end)


def transcript_paths(kb: KnowledgeBase) -> list[Path]:
    return [path for path, kind in scan_files(kb)
            if kind == "doc" and path.suffix.lower() == ".txt" and read_raw_transcript(path)]


def loop_summary(kb: KnowledgeBase) -> str | None:
    paths = transcript_paths(kb)
    if not paths:
        return None
    bad = sum(1 for path in paths if (result := check_file(path)) and result.spans)
    if bad:
        return f"Transcripts: {bad} of {len(paths)} have lost spans; run `genea transcripts check`."
    return f"Transcripts: {len(paths)} checked, no lost spans."
```

- [ ] **Step 4: Run the check tests**

Run: `uv run pytest tests/test_transcribe_check.py -q`
Expected: PASS.

- [ ] **Step 5: Commit the detection**

```bash
git add src/genea/transcribe/check.py tests/test_transcribe_check.py
git commit -m "Detect lost spans in transcripts: loops and early ends"
```

- [ ] **Step 6: Write the failing repair tests**

`tests/test_transcribe_repair.py`:

```python
import pytest
from fixtures.transcripts import LOOP_TEXT, loop_rows, speech_rows, whisper

from genea.ingest.transcript import Segment
from genea.transcribe.audio import SAMPLE_RATE
from genea.transcribe.repair import Replacement, RepairError, SpliceError, repair_transcript, splice

DURATION = 160.0


def make(tmp_path, rows, newline="\r\n", audio=True):
    path = tmp_path / "2023_x.txt"
    path.write_bytes(whisper(rows).replace("\n", newline).encode("utf-8"))
    if audio:
        (tmp_path / "2023_x.m4a").write_bytes(b"audio")
    return path


def run(path, script, duration=DURATION, **kwargs):
    """Repair `path`; the fake transcriber returns `script` (times relative to the cut)."""
    calls, said = [], []

    def transcriber(samples, language, on_segment):
        calls.append((len(samples), language))
        for start, end, text in script:
            on_segment(Segment(start, end, text))

    def load(audio, start, end):
        return range(round(((duration if end is None else end) - start) * SAMPLE_RATE))

    report = repair_transcript(path, path.parent / "work", transcriber=transcriber, language="sk", say=said.append,
                               duration_fn=lambda audio: duration, load_fn=load, **kwargs)
    return report, calls, said


TAIL = [(0.0, 5.0, "Nová veta."), (5.0, 110.0, "Dlhá veta.")]  # covers 50 s to the end of the 160 s audio


def lines(path):
    return path.read_bytes().decode("utf-8").split("\r\n")


def test_a_tail_loop_is_replaced_from_the_segment_before_it(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100))
    original = path.read_bytes()
    report, calls, said = run(path, TAIL)
    assert lines(path)[5] == "50.000000\t55.000000\t Nová veta."
    assert lines(path)[6] == "55.000000\t160.000000\t Dlhá veta."
    assert len(lines(path)) == 8 and lines(path)[7] == ""
    assert lines(path)[:5] == original.decode().split("\r\n")[:5]
    assert calls == [(110 * SAMPLE_RATE, "sk")]  # cut from 50 s to the end of the audio
    assert report.spliced and report.backup_created and not report.unreadable
    assert (tmp_path / "2023_x.txt.orig").read_bytes() == original
    assert any(line.startswith("[") and "Nová veta." in line for line in said)  # live progress was printed


def test_a_loop_in_the_middle_keeps_the_text_after_it(tmp_path):
    rows = speech_rows(6) + loop_rows(60, 100) + [(160.0, 170.0, "Potom.")]
    path = make(tmp_path, rows)
    run(path, [(0.0, 60.0, "Nová."), (60.0, 110.0, "Druhá.")], duration=170.0)
    assert lines(path)[-2] == "160.000000\t170.000000\t Potom."
    assert lines(path)[5] == "50.000000\t110.000000\t Nová."


def test_a_transcript_that_stops_early_is_extended(tmp_path):
    path = make(tmp_path, speech_rows(6))
    run(path, [(0.0, 110.0, "Zvyšok nahrávky.")])
    assert lines(path)[4].startswith("40.000000")
    assert lines(path)[5] == "50.000000\t160.000000\t Zvyšok nahrávky."


def test_from_replaces_everything_from_that_second(tmp_path):
    path = make(tmp_path, speech_rows(6))
    run(path, [(0.0, 140.0, "Nové.")], duration=160.0, from_s=30.0)
    assert lines(path)[2] == "20.000000\t160.000000\t Nové."  # one segment before the first one at or after 30 s
    assert lines(path)[1].startswith("10.000000")


def test_second_run_changes_nothing(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100))
    original = path.read_bytes()
    run(path, TAIL)
    done = path.read_bytes()
    report, calls, _ = run(path, TAIL)
    assert report.nothing_to_do and calls == []
    assert path.read_bytes() == done
    assert (tmp_path / "2023_x.txt.orig").read_bytes() == original  # still the first original


def test_an_existing_backup_is_never_overwritten(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100))
    (tmp_path / "2023_x.txt.orig").write_bytes(b"keep me")
    report, _, _ = run(path, TAIL)
    assert (tmp_path / "2023_x.txt.orig").read_bytes() == b"keep me" and not report.backup_created


def test_lf_file_stays_lf(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100), newline="\n")
    run(path, TAIL)
    assert b"\r" not in path.read_bytes()


def test_missing_final_newline_is_kept(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100))
    path.write_bytes(path.read_bytes().rstrip(b"\r\n"))
    run(path, TAIL)
    assert path.read_bytes().endswith("Dlhá veta.".encode()) and not path.read_bytes().endswith(b"\n")


def test_a_relooping_result_is_not_spliced(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100))
    original = path.read_bytes()
    report, _, _ = run(path, loop_rows(0, 70, "Zase to isté."))
    assert path.read_bytes() == original
    assert report.unreadable and not report.replaced and not report.spliced
    assert report.unreadable[0][1:] == (50.0, 120.0)  # the loop in the new text, on the recording's timeline


def test_empty_output_is_not_spliced(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100))
    original = path.read_bytes()
    report, _, _ = run(path, [])
    assert path.read_bytes() == original and report.unreadable


def test_no_splice_writes_only_the_work_file(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100))
    original = path.read_bytes()
    report, _, _ = run(path, TAIL, splice_result=False)
    assert path.read_bytes() == original and not report.spliced
    assert not (tmp_path / "2023_x.txt.orig").exists()
    assert report.work_file.read_bytes().decode().splitlines()[0] == "50.000000\t55.000000\t Nová veta."


def test_no_audio_is_an_error(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100), audio=False)
    with pytest.raises(RepairError, match="No audio file"):
        run(path, TAIL)


def test_not_a_transcript_is_an_error(tmp_path):
    path = tmp_path / "kronika.txt"
    path.write_text("Kronika obce Brno.\n", encoding="utf-8")
    with pytest.raises(RepairError, match="not a Whisper transcript"):
        run(path, TAIL)


def test_splice_only_touches_the_replaced_lines():
    text = "0.000000\t10.000000\t A\n10.000000\t20.000000\t B\n20.000000\t30.000000\t C"  # no final newline
    result = splice(text, [Replacement(1, 1, [Segment(10.0, 15.0, "x"), Segment(15.0, 20.0, "y")])])
    assert result == ("0.000000\t10.000000\t A\n10.000000\t15.000000\t x\n15.000000\t20.000000\t y\n"
                      "20.000000\t30.000000\t C")


def test_splice_refuses_to_put_timestamps_out_of_order():
    text = whisper([(0, 10, "A"), (10, 20, "B"), (20, 30, "C")])
    with pytest.raises(SpliceError, match="out of order"):
        splice(text, [Replacement(1, 1, [Segment(25.0, 26.0, "x")])])


def test_splice_refuses_overlapping_ranges():
    text = whisper([(0, 10, "A"), (10, 20, "B"), (20, 30, "C")])
    with pytest.raises(SpliceError, match="overlap"):
        splice(text, [Replacement(0, 1, [Segment(0, 1, "x")]), Replacement(1, 2, [Segment(1, 2, "y")])])
```

- [ ] **Step 7: Run them and see them fail**

Run: `uv run pytest tests/test_transcribe_repair.py -q`
Expected: FAIL (`ModuleNotFoundError: genea.transcribe.repair`).

- [ ] **Step 8: Implement `repair.py`**

```python
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
    starts = [s.start for s in parse_segments(result)]
    if starts != sorted(starts):
        raise SpliceError("the timestamps would go out of order")
    return result


def _spans(segments: list[Segment], audio_seconds: float | None, from_s: float | None) -> list[LostSpan]:
    if from_s is None:
        return find_lost_spans(segments, audio_seconds)
    first = next((i for i, s in enumerate(segments) if s.start >= from_s), len(segments))
    return [LostSpan("from", from_s, None, "", first, len(segments) - 1)]


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
    spans = _spans(segments, duration_fn(audio), from_s)
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
        for span in spans:
            before = max(span.first - 1, 0)  # start one segment earlier so a sentence is not cut in half
            cut_start = 0.0 if span.first == 0 else segments[before].start
            first_line = 0 if span.first == 0 else seg_lines[before]
            last_line = len(all_lines) - 1 if span.end is None else seg_lines[span.last]
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
            if not new:
                report.unreadable.append((span, cut_start, cut_start))
            elif loops:
                report.unreadable.append((span, loops[0].start, loops[0].end))
            else:
                replacements.append(Replacement(first_line, last_line, new))
                report.replaced.append((span, len(new)))
    say(summary_line(written, time.monotonic() - started, report.work_file))

    if splice_result and replacements:
        result = splice(text, replacements)
        temp = path.with_name(path.name + ".tmp")
        temp.write_bytes(result.encode("utf-8"))
        os.replace(temp, path)
        report.spliced = True
    return report
```

- [ ] **Step 9: Run the repair tests, then the whole suite**

Run: `uv run pytest tests/test_transcribe_repair.py -q`
Expected: PASS. Then `uv run pytest -q` to see nothing else broke. Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/genea/transcribe/repair.py tests/test_transcribe_repair.py
git commit -m "Re-transcribe lost spans of a transcript and splice them in safely"
```

---

### Task 7: The three CLI commands and the doctor line

**Files:**
- Create: `src/genea/cli/transcribecmds.py`, `tests/test_cli_transcribe.py`
- Modify: `src/genea/cli/app.py`, `src/genea/cli/main.py`, `src/genea/cli/kbcmds.py`
- Test: `tests/test_cli_transcribe.py`, `tests/test_cli_basic.py` (one doctor test)

**Interfaces:**
- Consumes: everything from Tasks 1-6; `common.open_kb`, `common.console` from `genea.cli.common`.
- Produces: commands `genea transcribe`, `genea transcripts check`, `genea transcripts repair`; module-level `make_transcriber(settings, model) -> Transcriber` (tests replace it); `transcripts_app` in `cli/app.py`.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli_transcribe.py`:

```python
import os
import re
import struct
import subprocess
import sys
import wave

import pytest
from fixtures.transcripts import loop_rows, speech_rows, whisper
from typer.testing import CliRunner

from genea.cli.main import app
from genea.ingest.transcript import Segment

pytest.importorskip("av")
runner = CliRunner()


def write_silence(path, seconds):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<h", 0) * (16000 * int(seconds)))


@pytest.fixture
def kb_with_loop(tmp_path):
    root = tmp_path / "Foto DB"  # a space in the folder name, like D:\Foto DB
    (root / "podcast").mkdir(parents=True)
    transcript = root / "podcast" / "2023_x-fabik.txt"
    transcript.write_text(whisper(speech_rows(6) + loop_rows(60, 100, "No a ja tam si sa učila.")), encoding="utf-8")
    write_silence(root / "podcast" / "2023_x-fabik.wav", 160)
    return root, transcript


class FakeTranscriber:
    def __init__(self, script):
        self.script = script
        self.calls = []

    def __call__(self, samples, language, on_segment):
        self.calls.append((len(samples), language))
        for start, end, text in self.script:
            on_segment(Segment(start, end, text))


def patch_transcriber(monkeypatch, script):
    fake = FakeTranscriber(script)
    monkeypatch.setattr("genea.cli.transcribecmds.make_transcriber", lambda settings, model: fake)
    return fake


def test_check_in_a_folder_with_a_space(kb_with_loop):
    root, _ = kb_with_loop
    result = runner.invoke(app, ["--kb", str(root), "transcripts", "check"])
    assert result.exit_code == 1, result.output
    assert "podcast/2023_x-fabik.txt" in result.output
    assert "dead span 1:00–end (100×" in result.output
    assert "real text: 0:00–1:00 of 2:40" in result.output


def test_check_of_a_clean_kb_exits_zero(tmp_path):
    (tmp_path / "good.txt").write_text(whisper(speech_rows(6)), encoding="utf-8")
    result = runner.invoke(app, ["--kb", str(tmp_path), "transcripts", "check"])
    assert result.exit_code == 0 and "No lost spans found." in result.output


def test_check_survives_cp1252_stdout(kb_with_loop, tmp_path):
    root, _ = kb_with_loop
    env = {**os.environ, "GENEA_HOME": str(tmp_path / "home"), "PYTHONIOENCODING": "cp1252"}
    env.pop("PYTHONUTF8", None)
    proc = subprocess.run([sys.executable, "-c", "from genea.cli.main import app; app()", "--kb", str(root),
                           "transcripts", "check"], env=env, capture_output=True, timeout=120)
    assert b"Traceback" not in proc.stderr, proc.stderr.decode("utf-8", "replace")
    assert "učila".encode() in proc.stdout  # the Slovak text is printed in UTF-8


def test_repair_splices_and_prints_live_progress(kb_with_loop, monkeypatch):
    root, transcript = kb_with_loop
    fake = patch_transcriber(monkeypatch, [(0.0, 5.0, "Nová veta."), (5.0, 110.0, "Dlhá veta.")])
    result = runner.invoke(app, ["--kb", str(root), "transcripts", "repair", str(transcript), "--language", "sk"])
    assert result.exit_code == 0, result.output
    assert re.search(r"\[\s*\d+\.\d%\] \d+:\d\d  elapsed [\d.]+m  eta (\?|[\d.]+m)  Nová veta\.", result.output)
    assert "Language: sk" in result.output and "Run `genea index`" in result.output
    assert fake.calls and fake.calls[0][1] == "sk"
    assert "Dlhá veta." in transcript.read_text(encoding="utf-8")
    assert transcript.with_name(transcript.name + ".orig").is_file()


def test_repair_with_auto_language_says_so(kb_with_loop, monkeypatch):
    root, transcript = kb_with_loop
    patch_transcriber(monkeypatch, [(0.0, 110.0, "x")])
    result = runner.invoke(app, ["--kb", str(root), "transcripts", "repair", str(transcript)])
    assert "Language: auto-detect" in result.output and "transcribe_language" in result.output


def test_repair_that_loops_again_exits_one_and_keeps_the_file(kb_with_loop, monkeypatch):
    root, transcript = kb_with_loop
    before = transcript.read_bytes()
    patch_transcriber(monkeypatch, loop_rows(0, 70, "Zase to isté."))
    result = runner.invoke(app, ["--kb", str(root), "transcripts", "repair", str(transcript)])
    assert result.exit_code == 1 and "still unreadable at 0:50–2:00" in result.output
    assert transcript.read_bytes() == before


def test_repair_without_audio_explains(kb_with_loop, monkeypatch):
    root, transcript = kb_with_loop
    transcript.with_suffix(".wav").unlink()
    result = runner.invoke(app, ["--kb", str(root), "transcripts", "repair", str(transcript)])
    assert result.exit_code == 1 and "No audio file" in result.output


def test_transcribe_writes_a_whisper_file_and_refuses_to_overwrite(tmp_path, monkeypatch):
    audio = tmp_path / "2023_y.wav"
    write_silence(audio, 30)
    patch_transcriber(monkeypatch, [(0.0, 10.0, "Dobrý deň."), (10.0, 25.0, "To sú kokosové.")])
    result = runner.invoke(app, ["transcribe", str(audio), "--start", "5", "--language", "sk"])
    assert result.exit_code == 0, result.output
    out = (tmp_path / "2023_y.txt").read_bytes().decode("utf-8")
    assert out == "5.000000\t15.000000\t Dobrý deň.\r\n15.000000\t30.000000\t To sú kokosové.\r\n"
    assert "2 segments written to" in result.output
    again = runner.invoke(app, ["transcribe", str(audio)])
    assert again.exit_code == 1 and "already exists" in again.output


def test_doctor_mentions_lost_spans(kb_with_loop, monkeypatch):
    root, _ = kb_with_loop
    monkeypatch.setattr("genea.cli.kbcmds.run_checks", lambda settings: [])
    result = runner.invoke(app, ["--kb", str(root), "doctor"])
    assert result.exit_code == 0 and "1 of 1 have lost spans" in result.output
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run pytest tests/test_cli_transcribe.py -q`
Expected: FAIL (the `transcripts` command does not exist: exit code 2 / "No such command").

- [ ] **Step 3: Register the sub-app**

In `src/genea/cli/app.py`, after the `summaries_app` lines add:

```python
transcripts_app = typer.Typer(no_args_is_help=True, help="Find and repair recordings whose transcript lost part of the speech.")
app.add_typer(transcripts_app, name="transcripts")
```

In `src/genea/cli/main.py` add `transcribecmds` to the import list (alphabetical: `..., summarycmds, transcribecmds`).

- [ ] **Step 4: Implement the commands**

`src/genea/cli/transcribecmds.py`:

```python
"""`genea transcribe` and `genea transcripts check / repair`."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.markup import escape

from genea.cli import common
from genea.cli.app import app, transcripts_app
from genea.config import KBNotFound, Settings, load_settings, resolve_kb_root
from genea.ingest.transcript import Segment, format_time
from genea.transcribe.audio import SAMPLE_RATE, AudioUnavailable, load_span
from genea.transcribe.check import check_file, describe_span, transcript_paths
from genea.transcribe.engine import (
    ModelUnavailable,
    Transcriber,
    WhisperCpp,
    language_label,
    resolve_language,
    resolve_model,
)
from genea.transcribe.progress import Progress
from genea.transcribe.repair import RepairError, SpliceError, format_segment, repair_transcript


def say(message: str) -> None:
    typer.echo(message)


def make_transcriber(settings: Settings, model: str | None) -> Transcriber:
    """The real whisper.cpp transcriber; tests replace this function."""
    return WhisperCpp(resolve_model(model or settings.transcribe_model, say), say=say)


def _fail(message: str) -> typer.Exit:
    common.console.print(f"[red]{escape(message)}[/red]")
    return typer.Exit(1)


def _settings(ctx: typer.Context) -> Settings:
    try:
        root = resolve_kb_root(ctx.obj["kb"])
    except KBNotFound:
        root = None
    return load_settings(root)


MODEL_OPTION = typer.Option(None, "--model", help="Model name (downloaded on first use) or path of a ggml .bin; "
                                                   "default: transcribe_model.")
LANGUAGE_OPTION = typer.Option(None, "--language", help="Language code such as sk; default: transcribe_language, "
                                                         "and when that is empty, auto-detect.")


@app.command()
def transcribe(
    ctx: typer.Context,
    audio: Path = typer.Argument(..., exists=True, dir_okay=False, help="The recording."),
    model: str | None = MODEL_OPTION,
    language: str | None = LANGUAGE_OPTION,
    start: float = typer.Option(0.0, "--start", min=0.0, help="Transcribe only from this second."),
    out: Path | None = typer.Option(None, "--out", help="Output file (default: next to the audio, .txt)."),
) -> None:
    """Transcribe a recording into a Whisper-format .txt, with live progress."""
    settings = _settings(ctx)
    target = out or audio.with_suffix(".txt")
    if target.exists():
        raise _fail(f"{target} already exists; pass --out to write elsewhere.")
    lang = resolve_language(language, settings.transcribe_language)
    say(f"Language: {language_label(lang)}")
    try:
        transcriber = make_transcriber(settings, model)
        samples = load_span(audio, start, None)
    except (ModelUnavailable, AudioUnavailable) as exc:
        raise _fail(str(exc)) from exc
    progress = Progress(len(samples) / SAMPLE_RATE, start)
    with target.open("w", encoding="utf-8", newline="") as file:
        def on_segment(s: Segment) -> None:
            file.write(format_segment(Segment(s.start + start, s.end + start, s.text)) + "\r\n")
            file.flush()
            say(progress.line(s.end, s.text))

        try:
            transcriber(samples, lang, on_segment)
        except KeyboardInterrupt:
            say(f"Interrupted; the part transcribed so far is kept in {target}")
            raise typer.Exit(130)
    say(progress.summary(target))


@transcripts_app.command()
def check(
    ctx: typer.Context,
    path: Path | None = typer.Argument(None, exists=True, dir_okay=False,
                                       help="One transcript; default: every transcript in the knowledge base."),
) -> None:
    """List transcripts where Whisper looped or stopped early. Exit code 1 when any are found."""
    kb = None if path else common.open_kb(ctx)
    paths = [path] if path else transcript_paths(kb)
    found = 0
    unchecked = False
    for candidate in paths:
        result = check_file(candidate)
        if result is None:
            continue
        unchecked = unchecked or result.audio_seconds is None
        if not result.spans:
            continue
        found += 1
        say(kb.rel(candidate) if kb else str(candidate))
        for span in result.spans:
            say(f"  {describe_span(span)}")
        total = f" of {format_time(result.audio_seconds)}" if result.audio_seconds else ""
        say(f"  real text: 0:00–{format_time(result.speech_end)}{total}")
    if not found:
        say("No lost spans found.")
    if unchecked:
        say("Some transcripts have no readable audio next to them (or the transcribe extra is missing), "
            "so early ends could not be checked for them.")
    raise typer.Exit(1 if found else 0)


@transcripts_app.command()
def repair(
    ctx: typer.Context,
    transcript: Path = typer.Argument(..., exists=True, dir_okay=False, help="The transcript to repair."),
    from_s: float | None = typer.Option(None, "--from", min=0.0, help="Re-transcribe from this second instead."),
    model: str | None = MODEL_OPTION,
    language: str | None = LANGUAGE_OPTION,
    no_splice: bool = typer.Option(False, "--no-splice", help="Only write the new text to .genea/work/."),
) -> None:
    """Re-transcribe the lost part of a transcript from its audio and splice it in."""
    kb = common.open_kb(ctx)
    lang = resolve_language(language, kb.settings.transcribe_language)
    say(f"Language: {language_label(lang)}")
    if not lang:
        say("The language is auto-detected; for a repair a fixed language is safer: "
            "set transcribe_language in the KB config or pass --language.")
    holder: list[Transcriber] = []

    def transcriber(samples, language_code, on_segment) -> None:
        if not holder:  # the model is loaded only when there is something to transcribe
            holder.append(make_transcriber(kb.settings, model))
        holder[0](samples, language_code, on_segment)

    try:
        report = repair_transcript(transcript, kb.meta_dir / "work", transcriber=transcriber, language=lang,
                                   say=say, from_s=from_s, splice_result=not no_splice)
    except (ModelUnavailable, AudioUnavailable, RepairError, SpliceError) as exc:
        raise _fail(str(exc)) from exc
    except KeyboardInterrupt:
        say("Interrupted; the transcript is untouched and the part transcribed so far stays in .genea/work/")
        raise typer.Exit(130)
    if report.nothing_to_do:
        say("Nothing to repair: no lost spans found.")
        return
    for span, count in report.replaced:
        end = "end" if span.end is None else format_time(span.end)
        say(f"Replaced {format_time(span.start)}–{end} with {count} new segments")
    for span, start, end in report.unreadable:
        say(f"still unreadable at {format_time(start)}–{format_time(end)}: left as it was")
    if report.spliced:
        say("Run `genea index` to re-index this transcript (its summary is regenerated).")
    elif no_splice:
        say(f"Not spliced (--no-splice). The new text is in {report.work_file}")
    if report.unreadable:
        raise typer.Exit(1)
```

- [ ] **Step 5: Add the doctor line**

In `src/genea/cli/kbcmds.py`: import `from genea.kb import KnowledgeBase, init_kb` and `from genea.transcribe.check import loop_summary`; in `doctor`, store `settings = load_settings(root)`, loop over `run_checks(settings)` as before, and after the loop (before `raise typer.Exit`) add:

```python
    if root is not None:
        note = loop_summary(KnowledgeBase(root, settings))
        if note:
            console.print(f"[dim]{note}[/dim]")  # informational: it never changes the exit code
```

- [ ] **Step 6: Run the CLI tests and the whole suite**

Run: `uv run pytest tests/test_cli_transcribe.py tests/test_cli_basic.py -q`
Expected: PASS. Then `uv run pytest -q`. Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/genea/cli tests/test_cli_transcribe.py
git commit -m "Add the transcribe, transcripts check and transcripts repair commands"
```

---

### Task 8: README and a live test

**Files:**
- Modify: `README.md` (commands table around line 90; new section after "Transcripts", before "Privacy")
- Create: `tests/live/test_live_transcribe.py`

- [ ] **Step 1: Document the feature**

Add three rows to the commands table:

```
| `genea transcribe <audio> [--model M] [--language L] [--start S] [--out F]` | Transcribe a recording with whisper.cpp into a Whisper-format `.txt`, with live progress |
| `genea transcripts check [path]` | List transcripts where Whisper looped or stopped early (exit code 1 if any) |
| `genea transcripts repair <transcript> [--from S] [--model M] [--language L] [--no-splice]` | Re-transcribe the lost part from the audio and splice it in |
```

Add after the "Transcripts" section:

```markdown
## Transcribing and repairing recordings

Whisper sometimes loops on one line until the end of a recording, or stops early. The index then holds an
incomplete recording without saying so. `genea transcripts check` finds these (a run of at least three identical
segments lasting a minute or more, or a transcript that ends a minute or more before its audio) and
`genea doctor` mentions them. `genea transcripts repair <transcript>` cuts the audio from the segment before the
lost part, transcribes it again, and splices the result in. The first original is kept as `<name>.txt.orig`,
other lines are not touched, and a result that loops again is not spliced. Run `genea index` afterwards:
only that transcript is indexed again and its summary regenerated.

`genea transcribe <audio>` makes a transcript from scratch. Both commands print a line per decoded segment:
`[ 98.1%] 39:55  elapsed 11.0m  eta 0.2m  To sú kokosové.`

They need the extra `transcribe` (`uv sync --extra transcribe`; `check` works without it but cannot read audio
lengths). The model is `transcribe_model` (default `medium`; a name such as `large-v3` is downloaded on first
use, or give the path of a ggml `.bin`, for example the models of Audacity's OpenVINO plugin). `--language`
or `transcribe_language` fixes the language (`sk`); when neither is set it is auto-detected, which can go wrong
on a short span, so set it for a repair. The audio must sit next to the transcript with the same name
(`.m4a`, `.mp3`, `.wav`, `.mp4`, `.ogg` or `.flac`).
```

Add to the config table: `transcribe_model` (`medium`) and `transcribe_language` (`""`).

- [ ] **Step 2: Write the live test**

`tests/live/test_live_transcribe.py`:

```python
"""A real whisper.cpp run: needs GENEA_TEST_AUDIO (a short recording) and GENEA_TEST_MODEL (a ggml .bin or a model name).

Run with `uv run pytest -m live tests/live/test_live_transcribe.py`.
"""

import os

import pytest

from genea.transcribe.audio import load_span
from genea.transcribe.engine import WhisperCpp, resolve_model

pytestmark = pytest.mark.live


def test_a_real_model_transcribes_ten_seconds():
    pytest.importorskip("pywhispercpp")
    audio, model = os.environ.get("GENEA_TEST_AUDIO"), os.environ.get("GENEA_TEST_MODEL")
    if not audio or not model:
        pytest.skip("set GENEA_TEST_AUDIO and GENEA_TEST_MODEL")
    segments = []
    WhisperCpp(resolve_model(model))(load_span(audio, 0.0, 10.0), "", segments.append)
    assert segments and all(s.end >= s.start for s in segments)
```

- [ ] **Step 3: Run the suite and a manual smoke test**

Run: `uv run pytest -q` (Expected: PASS; the live test is deselected). Then, with the real model and the repaired recording folder:
`uv run genea --kb "D:\Foto DB" transcripts check` (Expected: lists any remaining lost spans, or "No lost spans found.").

- [ ] **Step 4: Commit**

```bash
git add README.md tests/live/test_live_transcribe.py
git commit -m "Document transcribing and repairing recordings; add a live transcription test"
```

---

## Self-Review

**Spec coverage**
- Section 1 decisions: engine/extra (Task 2), model setting and `--model` (Tasks 2, 5, 7), detection at read time (Tasks 1, 6), CLI only (Task 7; no MCP file is touched), language setting and flag (Tasks 2, 5, 7), UTF-8 console (already provided by `use_utf8_stdout`; pinned by `test_check_survives_cp1252_stdout`).
- Section 2 commands and live progress: Task 7 (commands), Task 3 (format). `--start` on `transcribe`: Task 7.
- Section 3 detection incl. early end and `check` without the extra: Task 1 (`dead_spans`), Task 6 (`find_lost_spans`, `check_file`; `audio_duration` returns None without `av`).
- Section 4 repair steps 1-6 and re-looping: Task 6 (backup first, preceding segment, work file, atomic splice, validation, reloop skip). One deliberate reading: with `--no-splice` no `.orig` is created, because nothing in the transcript changes (tested by `test_no_splice_writes_only_the_work_file`). A re-looping lost span is skipped as a whole rather than part by part.
- Section 5 language and model: Task 5 (names are validated against `pywhispercpp.constants.AVAILABLE_MODELS`, so the spec's open question about valid names is answered by the library itself).
- Section 6 errors: extra missing (`EXTRA_HINT`, Tasks 4-5), no audio (Task 6), Ctrl+C (Task 7, partial output in `.genea/work/` written per segment), existing output file (Task 7), missing `.orig` (Task 6), `check` without `av` (Task 7 message).
- Section 7 layout and 8 testing: all files exist in the File Structure table; the live test is Task 8.

**Placeholder scan:** no TBD/TODO; every code step has code.

**Type consistency:** `Transcriber` is called as `transcriber(samples, language, on_segment)` in `repair_transcript`, `transcribe`, `WhisperCpp.__call__` and every fake. `LostSpan(kind, start, end, text, first, last)` has the same field order in `check.py` and the tests. `Replacement(first_line, last_line, segments)` matches between `splice` and the tests. `Progress.line(end_s, text)` takes times relative to the cut everywhere. `make_transcriber(settings, model)` has the same two parameters in the module and in the test lambdas.
