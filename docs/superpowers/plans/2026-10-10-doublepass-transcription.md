# Double-pass transcription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `genea transcribe` can run a small generic model first to find segment boundaries, then an accurate model (e.g. `sk-large-v3-turbo`) on each segment's audio, and write the first model's boundaries with the second model's text.

**Architecture:** A new pure module `genea.transcribe.doublepass` takes two transcriber factories (the existing `Transcriber` callable type), cleans pass 1's segments, slices the in-memory samples with 0.3 s padding for pass 2 and emits `Segment`s. The CLI builds the real transcribers one after the other and writes through the existing `.part` file and rename.

**Tech Stack:** Python 3.12, typer, pywhispercpp (optional extra, faked in tests), pytest.

**Spec:** `docs/superpowers/specs/2026-10-10-doublepass-transcription-design.md`

## Global Constraints

- Without `--structure-model` and with `transcribe_structure_model` empty (the default), `genea transcribe` behaves exactly as today (existing tests in `tests/test_cli_transcribe.py` stay green unchanged, apart from the `FakeSamples` helper gaining `__getitem__`).
- Pass 2 uses `--model` / `transcribe_model`; pass 1 uses `--structure-model` / `transcribe_structure_model`. `--structure-model ""` turns the double pass off when the setting is set.
- Only one model is in memory at a time; both model names are resolved before any audio is decoded.
- Constants (starting values): minimum segment 1.0 s, padding 0.3 s, length ratio 4, ratio applies only when pass 1's text has at least 20 characters.
- A segment whose pass 2 text is empty or loops is dropped; pass 1's text is never used as a fallback. A length mismatch keeps pass 2's text and prints a `check:` line.
- Output is the unchanged Whisper format (`start<TAB>end<TAB> text`, CRLF) written to `<name>.txt.part`, then renamed. Only pass 2 segments reach the file.
- Out of scope: diarization, word-level alignment, double pass in `transcripts repair`, any index/schema/MCP change.
- Code style: match the surrounding code (type hints, `from __future__ import annotations`, short docstrings, lines up to 120 characters).

## Review Focus

1. A recording whose only pass 1 segment is shorter than 1.0 s: the segment is kept, not dropped or merged into nothing (Task 2).
2. Pass 1 cannot detect the language and no language is set: say so and carry on with auto-detect (Task 2).
3. `--start 5`: output times are offset by 5 and padding is applied relative to the cut audio (Task 3).
4. Ctrl+C during pass 2: `.part` holds only finished pass 2 text and the real file is not written (Task 3).
5. Pass 1 loops to the end of the recording (a dead span): the loop is dropped, and if nothing else remains the run stops with "no speech" and writes nothing (Task 2 and Task 3).

Known risk, not tested: the 0.3 s padding makes neighbouring slices overlap, so a word at a boundary can appear in both segments. The manual comparison in Task 4 looks for it.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/genea/config.py` (modify) | New setting `transcribe_structure_model` |
| `src/genea/transcribe/engine.py` (modify) | `WhisperCpp.detected_language` so pass 2 can reuse pass 1's language |
| `src/genea/transcribe/doublepass.py` (create) | `NoSpeech`, `clean_boundaries`, `double_pass` (pure, models injected) |
| `src/genea/cli/transcribecmds.py` (modify) | `--structure-model`, `ensure_models`, progress prefixes, calls `double_pass` |
| `tests/test_config.py`, `tests/test_transcribe_engine.py`, `tests/test_transcribe_doublepass.py` (create), `tests/test_cli_transcribe.py` | Tests |
| `README.md`, spec | Documentation and two spec corrections |

---

### Task 1: Setting and detected language

**Files:**
- Modify: `src/genea/config.py:26`
- Modify: `src/genea/transcribe/engine.py:90-117`
- Test: `tests/test_config.py`, `tests/test_transcribe_engine.py`

**Interfaces:**
- Produces: `Settings.transcribe_structure_model: str = ""`; `WhisperCpp.detected_language: str` (class attribute `""`, set to the detected code after a call that auto-detected; `""` otherwise).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_structure_model_is_off_by_default_and_can_be_set(isolated_home, tmp_path):
    assert load_settings(None).transcribe_structure_model == ""
    kb = tmp_path / "kb"
    (kb / ".genea").mkdir(parents=True)
    (kb / ".genea" / "config.toml").write_text('transcribe_structure_model = "base"\n', encoding="utf-8")
    assert load_settings(kb).transcribe_structure_model == "base"
```

Append to `tests/test_transcribe_engine.py`:

```python
def test_the_detected_language_is_kept_for_the_next_pass():
    transcriber, _ = whisper_with(FakeModel("sk"))
    assert transcriber.detected_language == ""
    transcriber([0.0] * 10, "", lambda s: None)
    assert transcriber.detected_language == "sk"
    transcriber, _ = whisper_with(FakeModel(fails=True))
    transcriber([0.0], "", lambda s: None)
    assert transcriber.detected_language == ""  # detection failed: nothing to pass on
    transcriber, _ = whisper_with(FakeModel("sk"))
    transcriber([0.0], "cs", lambda s: None)
    assert transcriber.detected_language == ""  # a fixed language is not "detected"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_config.py tests/test_transcribe_engine.py -v`
Expected: FAIL (`AttributeError: ... 'transcribe_structure_model'` and `... 'detected_language'`).

- [ ] **Step 3: Implement**

In `src/genea/config.py`, after the `transcribe_language` line add:

```python
    transcribe_structure_model: str = ""  # small model whose segment boundaries the double pass keeps; empty: off
```

In `src/genea/transcribe/engine.py`, in `class WhisperCpp`, add a class attribute right under the docstring:

```python
    detected_language = ""  # set by a call that auto-detected the language, so a second pass can reuse it
```

and in `__call__` replace the `if not language:` block's tail so the code reads:

```python
    def __call__(self, samples, language: str, on_segment: Callable[[Segment], None]) -> None:
        self.detected_language = ""
        if not language:
            try:
                detected = self._model.auto_detect_language(samples)[0][0]
            except Exception:  # noqa: BLE001 - whisper.cpp then detects the language itself
                detected = ""
            if detected:
                self._say(f"Detected language: {detected}")
                language = detected  # passed on, so whisper.cpp does not detect it a second time
                self.detected_language = detected
```

(The rest of the method is unchanged.)

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_config.py tests/test_transcribe_engine.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/genea/config.py src/genea/transcribe/engine.py tests/test_config.py tests/test_transcribe_engine.py
git commit -m "Add transcribe_structure_model setting and keep the detected language"
```

---

### Task 2: The double-pass module

**Files:**
- Create: `src/genea/transcribe/doublepass.py`
- Test: `tests/test_transcribe_doublepass.py`
- Modify: `docs/superpowers/specs/2026-10-10-doublepass-transcription-design.md` (two corrections, last step)

**Interfaces:**
- Consumes: `Segment`, `find_runs`, `format_time` from `genea.ingest.transcript`; `SAMPLE_RATE` from `genea.transcribe.audio`; `Transcriber` (`Callable[..., None]`, called `transcriber(samples, language, on_segment)`) from `genea.transcribe.engine`.
- Produces:
  - `class NoSpeech(RuntimeError)`
  - `clean_boundaries(segments: list[Segment], min_seconds: float = 1.0) -> list[Segment]`
  - `double_pass(samples, language: str, make_structure: Callable[[], Transcriber], make_text: Callable[[], Transcriber], on_segment: Callable[[Segment], None], *, say: Callable[[str], None] = ..., on_structure: Callable[[Segment], None] = ..., on_text_start: Callable[[], None] = ...) -> None`. `samples` supports `len()` and slicing; times are seconds from `samples[0]`, as for every transcriber.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_transcribe_doublepass.py`:

```python
import weakref

import pytest

from genea.ingest.transcript import Segment
from genea.transcribe.audio import SAMPLE_RATE
from genea.transcribe.doublepass import NoSpeech, clean_boundaries, double_pass


class Samples:
    """Stands in for audio: slicing returns the (start, end) seconds, so tests see which span was cut."""

    def __init__(self, seconds):
        self.count = round(seconds * SAMPLE_RATE)

    def __len__(self):
        return self.count

    def __getitem__(self, key):
        return (key.start / SAMPLE_RATE, key.stop / SAMPLE_RATE)


class Structure:
    def __init__(self, rows, detected=""):
        self.rows, self.detected_language = rows, detected

    def __call__(self, samples, language, on_segment):
        for start, end, text in self.rows:
            on_segment(Segment(start, end, text))


class Text:
    """Pass 2 stand-in: `answers` maps a cut span to the texts to return, in order; others return one 'T'."""

    def __init__(self, answers=None):
        self.answers, self.calls = answers or {}, []

    def __call__(self, samples, language, on_segment):
        self.calls.append((samples, language))
        for text in self.answers.get(samples, ["T"]):
            on_segment(Segment(0.0, 1.0, text))


def run(structure, text, seconds=30, language="sk", **hooks):
    out = []
    double_pass(Samples(seconds), language, lambda: structure, lambda: text, out.append, **hooks)
    return out


def test_boundaries_come_from_pass_one_and_text_from_pass_two():
    text = Text({(0.0, 10.3): ["Prvý."], (9.7, 25.3): ["Druhý.", "Veta."]})
    out = run(Structure([(0.0, 10.0, "aaaa"), (10.0, 25.0, "bbbb")]), text)
    assert out == [Segment(0.0, 10.0, "Prvý."), Segment(10.0, 25.0, "Druhý. Veta.")]
    assert [call[0] for call in text.calls] == [(0.0, 10.3), (9.7, 25.3)]  # padded, clamped at 0


def test_padding_is_clamped_at_the_end_of_the_recording():
    text = Text()
    run(Structure([(20.0, 30.0, "aaaa")]), text, seconds=30)
    assert text.calls[0][0] == (19.7, 30.0)


def test_short_segments_merge_into_a_neighbour_and_the_first_merges_forward():
    rows = [Segment(0.0, 0.5, "x"), Segment(0.5, 10.0, "y"), Segment(10.0, 10.4, "z"), Segment(10.4, 20.0, "w")]
    assert clean_boundaries(rows) == [Segment(0.0, 10.4, "x y z"), Segment(10.4, 20.0, "w")]


def test_a_recording_with_only_a_short_segment_keeps_it():
    assert clean_boundaries([Segment(0.0, 0.5, "x")]) == [Segment(0.0, 0.5, "x")]


def test_looped_and_empty_pass_one_segments_are_dropped_before_pass_two():
    loop = [(float(i), float(i + 2), "Dookola.") for i in range(0, 10, 2)]
    text = Text()
    out = run(Structure([*loop, (10.0, 14.0, "  "), (14.0, 20.0, "skutočná veta")]), text)
    assert len(text.calls) == 1 and out == [Segment(14.0, 20.0, "T")]


def test_pass_two_text_that_is_empty_or_loops_drops_the_segment_without_falling_back():
    said = []
    text = Text({(0.0, 10.3): [], (9.7, 20.3): ["Zase.", "Zase.", "Zase."]})
    out = run(Structure([(0.0, 10.0, "pass one text"), (10.0, 20.0, "more pass one text")]), text, say=said.append)
    assert out == [] and len(said) >= 2


def test_a_text_length_that_differs_a_lot_is_kept_but_flagged():
    said = []
    long_text = "dlhý text " * 20
    out = run(Structure([(0.0, 10.0, "a short guess of thirty chars")]), Text({(0.0, 10.3): [long_text]}),
              say=said.append)
    assert out == [Segment(0.0, 10.0, long_text)]
    assert any(line.startswith("check:") and "0:00" in line for line in said)


def test_a_short_pass_one_text_is_not_compared():
    said = []
    run(Structure([(0.0, 10.0, "ab")]), Text({(0.0, 10.3): ["x" * 200]}), say=said.append)
    assert not any(line.startswith("check:") for line in said)


def test_nothing_usable_in_pass_one_stops_before_the_text_model_is_made():
    made = []
    loop = [(float(i), float(i + 2), "Dookola.") for i in range(0, 10, 2)]
    with pytest.raises(NoSpeech, match="no speech"):
        double_pass(Samples(30), "sk", lambda: Structure(loop), lambda: made.append("text"), lambda s: None)
    assert made == []


def test_the_structure_model_is_released_before_the_text_model_is_made():
    refs = []

    def make_structure():
        model = Structure([(0.0, 5.0, "aaaa")])
        refs.append(weakref.ref(model))
        return model

    def make_text():
        assert refs[0]() is None, "the structure model is still in memory"
        return Text()

    out = []
    double_pass(Samples(30), "sk", make_structure, make_text, out.append)
    assert len(out) == 1


def test_pass_two_reuses_the_language_pass_one_detected_unless_one_is_set():
    text = Text()
    run(Structure([(0.0, 5.0, "aaaa")], detected="sk"), text, language="")
    assert text.calls[0][1] == "sk"
    text = Text()
    run(Structure([(0.0, 5.0, "aaaa")], detected="cs"), text, language="sk")
    assert text.calls[0][1] == "sk"


def test_without_any_language_it_says_so_and_goes_on():
    said = []
    text = Text()
    out = run(Structure([(0.0, 5.0, "aaaa")]), text, language="", say=said.append)
    assert len(out) == 1 and text.calls[0][1] == ""
    assert any("language" in line and "--language" in line for line in said)


def test_the_hooks_fire_in_order():
    events = []
    structure = Structure([(0.0, 5.0, "aaaa"), (5.0, 9.0, "bbbb")])
    text = Text()
    double_pass(Samples(30), "sk", lambda: structure, lambda: (events.append("make_text"), text)[1],
                lambda s: events.append("segment"), on_structure=lambda s: events.append("structure"),
                on_text_start=lambda: events.append("text_start"))
    assert events == ["structure", "structure", "make_text", "text_start", "segment", "segment"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_transcribe_doublepass.py -v`
Expected: FAIL (`ModuleNotFoundError: genea.transcribe.doublepass`).

- [ ] **Step 3: Write the implementation**

Create `src/genea/transcribe/doublepass.py`:

```python
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
```

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run pytest tests/test_transcribe_doublepass.py -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Correct the spec to match the implementation**

In `docs/superpowers/specs/2026-10-10-doublepass-transcription-design.md`:
- Section 3 step 2 and section 4: the loop detection is `find_runs` in `genea.ingest.transcript` (3 or more identical segments in a row), not `genea.transcribe.check`; the dependencies column of the `doublepass` row names `genea.ingest.transcript`.
- Section 3 step 3 and section 4: pass 2 cuts the span from the samples already in memory (slicing), it does not call `load_span` again; `double_pass` takes `(samples, language, make_structure, make_text, on_segment, *, say, on_structure, on_text_start)` where the two models are factories so the first can be released before the second is made. Add that the language-not-detected case prints a hint and continues.

- [ ] **Step 6: Commit**

```bash
git add src/genea/transcribe/doublepass.py tests/test_transcribe_doublepass.py docs/superpowers/specs/2026-10-10-doublepass-transcription-design.md
git commit -m "Add double_pass: boundaries from a small model, text from an accurate one"
```

---

### Task 3: `genea transcribe --structure-model`

**Files:**
- Modify: `src/genea/cli/transcribecmds.py` (imports, new `ensure_models`, `STRUCTURE_OPTION`, the `transcribe` command at lines 61-107)
- Test: `tests/test_cli_transcribe.py`

**Interfaces:**
- Consumes: `double_pass`, `NoSpeech` from Task 2; `Settings.transcribe_structure_model` from Task 1; existing `make_transcriber(settings, model)`, `resolve_model`, `Progress(total_s, offset_s)` (`.line(end_s, text)`, `.count`, `.elapsed()`, `.summary(path)`).
- Produces: `ensure_models(settings: Settings, names: list[str | None]) -> None` (module-level, tests replace it); option `--structure-model`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli_transcribe.py`, give `FakeSamples` slicing so the double pass can cut spans without `av`. Replace the class with:

```python
class FakeSamples:
    """Stands in for the samples of a span: only its length is used."""

    def __init__(self, count):
        self.count = count

    def __len__(self):
        return self.count

    def __getitem__(self, key):
        return FakeSamples(max(0, min(key.stop, self.count) - key.start))
```

Append at the end of the file:

```python
def patch_two_models(monkeypatch, structure_rows, texts=None, text_effect=None):
    """make_transcriber: 'base' is the structure model, anything else numbers its texts T1, T2, ..."""
    made, ensured, counter = [], [], iter(range(1, 100))

    def fake_make(settings, model):
        made.append(model)
        if model == "base":
            return FakeTranscriber(structure_rows)

        def text_model(samples, language, on_segment):
            n = next(counter)
            if text_effect:
                text_effect(n)
            on_segment(Segment(0.0, 1.0, (texts or {}).get(n, f"T{n}")))

        return text_model

    monkeypatch.setattr("genea.cli.transcribecmds.make_transcriber", fake_make)
    monkeypatch.setattr("genea.cli.transcribecmds.ensure_models", lambda settings, names: ensured.append(names))
    return made, ensured


def test_double_pass_writes_pass_one_boundaries_with_pass_two_text(tmp_path, monkeypatch):
    audio = tmp_path / "2023_y.wav"
    write_silence(audio, 30)
    made, ensured = patch_two_models(monkeypatch, [(0.0, 10.0, "aaaa bbbb"), (10.0, 25.0, "cccc dddd")])
    result = runner.invoke(app, ["transcribe", str(audio), "--structure-model", "base",
                                 "--model", "sk-large-v3-turbo", "--language", "sk"])
    assert result.exit_code == 0, result.output
    out = (tmp_path / "2023_y.txt").read_bytes().decode("utf-8")
    assert out == "0.000000\t10.000000\t T1\r\n10.000000\t25.000000\t T2\r\n"
    assert ensured == [["base", "sk-large-v3-turbo"]]  # both names resolved before any audio is decoded
    assert made == ["base", "sk-large-v3-turbo"]
    assert "[1/2 structure]" in result.output and "[2/2 text]" in result.output
    assert "Structure pass finished" in result.output and "2 segments written to" in result.output
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2023_y.txt", "2023_y.wav"]


def test_double_pass_offsets_the_times_by_start(tmp_path, monkeypatch):
    audio = tmp_path / "2023_y.wav"
    write_silence(audio, 30)
    patch_two_models(monkeypatch, [(0.0, 10.0, "aaaa bbbb")])
    result = runner.invoke(app, ["transcribe", str(audio), "--structure-model", "base", "--start", "5",
                                 "--language", "sk"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "2023_y.txt").read_bytes() == b"5.000000\t15.000000\t T1\r\n"


def test_the_setting_turns_the_double_pass_on_and_an_empty_flag_turns_it_off(isolated_home, tmp_path, monkeypatch):
    (tmp_path / ".genea").mkdir()
    (tmp_path / ".genea" / "config.toml").write_text('transcribe_structure_model = "base"\n', encoding="utf-8")
    audio = tmp_path / "2023_y.wav"
    write_silence(audio, 30)
    made, ensured = patch_two_models(monkeypatch, [(0.0, 10.0, "aaaa bbbb")])
    result = runner.invoke(app, ["--kb", str(tmp_path), "transcribe", str(audio), "--language", "sk"])
    assert result.exit_code == 0, result.output
    assert made == ["base", None] and ensured == [["base", None]]  # None: the transcribe_model setting
    (tmp_path / "2023_y.txt").unlink()
    made.clear()
    ensured.clear()
    result = runner.invoke(app, ["--kb", str(tmp_path), "transcribe", str(audio), "--structure-model", "",
                                 "--language", "sk"])
    assert result.exit_code == 0, result.output
    assert made == [None] and ensured == []  # one model, as before
    assert "[1/2 structure]" not in result.output


def test_an_unknown_structure_model_fails_before_any_audio_is_decoded(tmp_path, monkeypatch):
    from genea.transcribe.engine import ModelUnavailable

    audio = tmp_path / "2023_y.wav"
    write_silence(audio, 30)

    def missing(settings, names):
        raise ModelUnavailable("'nope' is neither a file nor a known model name.")

    def no_audio(*args, **kwargs):
        raise AssertionError("the audio was decoded")

    monkeypatch.setattr("genea.cli.transcribecmds.ensure_models", missing)
    monkeypatch.setattr("genea.cli.transcribecmds.load_span", no_audio)
    result = runner.invoke(app, ["transcribe", str(audio), "--structure-model", "nope"])
    assert result.exit_code == 1 and "neither a file nor a known model name" in result.output
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2023_y.wav"]


def test_a_structure_model_that_hears_nothing_stops_and_writes_nothing(tmp_path, monkeypatch):
    audio = tmp_path / "2023_y.wav"
    write_silence(audio, 30)
    loop = [(float(i), float(i + 2), "Dookola.") for i in range(0, 12, 2)]  # pass 1 looped on silence
    patch_two_models(monkeypatch, loop)
    result = runner.invoke(app, ["transcribe", str(audio), "--structure-model", "base", "--language", "sk"])
    assert result.exit_code == 1 and "found no speech" in result.output
    assert isinstance(result.exception, SystemExit)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2023_y.wav"]  # no .txt and no .txt.part


def test_ctrl_c_in_pass_two_keeps_only_finished_text_in_the_part_file(tmp_path, monkeypatch):
    audio = tmp_path / "2023_y.wav"
    write_silence(audio, 30)

    def interrupt_on_second(n):
        if n == 2:
            raise KeyboardInterrupt

    patch_two_models(monkeypatch, [(0.0, 10.0, "aaaa bbbb"), (10.0, 25.0, "cccc dddd")],
                     text_effect=interrupt_on_second)
    result = runner.invoke(app, ["transcribe", str(audio), "--structure-model", "base", "--language", "sk"])
    assert result.exit_code == 130 and "2023_y.txt.part" in result.output
    assert not (tmp_path / "2023_y.txt").exists()
    assert (tmp_path / "2023_y.txt.part").read_bytes() == b"0.000000\t10.000000\t T1\r\n"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_cli_transcribe.py -v`
Expected: the six new tests FAIL (no such option `--structure-model` / `ensure_models` missing); the old tests still pass.

- [ ] **Step 3: Implement**

In `src/genea/cli/transcribecmds.py`:

Add to the imports (keep the block sorted as the file does):

```python
from genea.transcribe.doublepass import NoSpeech, double_pass
```

Below `make_transcriber` add:

```python
def ensure_models(settings: Settings, names: list[str | None]) -> None:
    """Finds, downloads or converts each model now, so a wrong name fails before any audio is decoded."""
    for name in names:
        resolve_model(name or settings.transcribe_model, say)
```

Below `LANGUAGE_OPTION` add:

```python
STRUCTURE_OPTION = typer.Option(None, "--structure-model",
                                help="Double pass: a small model (such as base) finds the segment boundaries, "
                                     "then --model writes the text of each. Default: transcribe_structure_model; "
                                     "empty means a single pass.")
```

Replace the whole `transcribe` command with:

```python
@app.command()
def transcribe(
    ctx: typer.Context,
    audio: Path = typer.Argument(..., exists=True, dir_okay=False, help="The recording."),
    model: str | None = MODEL_OPTION,
    structure_model: str | None = STRUCTURE_OPTION,
    language: str | None = LANGUAGE_OPTION,
    start: float = typer.Option(0.0, "--start", min=0.0, help="Transcribe only from this second."),
    out: Path | None = typer.Option(None, "--out", help="Output file (default: next to the audio, .txt)."),
) -> None:
    """Transcribe a recording into a Whisper-format .txt, with live progress."""
    settings = _settings(ctx)
    target = out or audio.with_suffix(".txt")
    if target.exists():
        raise _fail(f"{target} already exists; pass --out to write elsewhere.")
    if not target.parent.is_dir():
        raise _fail(f"The folder {target.parent} does not exist; create it or pass another --out.")
    lang = resolve_language(language, settings.transcribe_language)
    say(f"Language: {language_label(lang)}")
    structure = settings.transcribe_structure_model if structure_model is None else structure_model
    transcriber = None
    try:
        if structure:
            ensure_models(settings, [structure, model])  # both names are checked before pass 1 starts
            say(f"Double pass: boundaries from {structure}, text from {model or settings.transcribe_model}")
        else:
            transcriber = make_transcriber(settings, model)
        samples = load_span(audio, start, None)
    except (ModelUnavailable, AudioUnavailable) as exc:
        raise _fail(str(exc)) from exc
    total = len(samples) / SAMPLE_RATE
    progress = Progress(total, start)
    prefix = "[2/2 text] " if structure else ""
    # Written aside and renamed at the end, so a failed run never leaves a partial transcript under the
    # real name (it would be indexed, and the next run would refuse to overwrite it).
    part = target.with_name(target.name + ".part")
    try:
        with part.open("w", encoding="utf-8", newline="") as file:
            def on_segment(s: Segment) -> None:
                file.write(format_segment(Segment(s.start + start, s.end + start, s.text)) + "\r\n")
                file.flush()
                say(prefix + progress.line(s.end, s.text))

            if structure:
                structure_progress = Progress(total, start)

                def on_structure(s: Segment) -> None:
                    say("[1/2 structure] " + structure_progress.line(s.end, s.text))

                def on_text_start() -> None:
                    nonlocal progress
                    say(f"Structure pass finished in {structure_progress.elapsed() / 60:.1f}m")
                    progress = Progress(total, start)  # the estimate of pass 2 must not include pass 1

                double_pass(samples, lang, lambda: make_transcriber(settings, structure),
                            lambda: make_transcriber(settings, model), on_segment, say=say,
                            on_structure=on_structure, on_text_start=on_text_start)
            else:
                transcriber(samples, lang, on_segment)
    except KeyboardInterrupt:
        say(f"Interrupted; {target.name} was not written. The part transcribed so far is in {part} "
            "(a new run starts over and replaces it).")
        raise typer.Exit(130) from None
    except (ModelUnavailable, NoSpeech) as exc:
        part.unlink(missing_ok=True)
        raise _fail(str(exc)) from exc
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    try:
        os.replace(part, target)
    except OSError as exc:
        raise _fail(f"Could not rename {part} to {target.name}: {exc}") from exc
    say(progress.summary(target))
```

- [ ] **Step 4: Run the transcribe tests and the whole suite**

Run: `uv run pytest tests/test_cli_transcribe.py -v` then `uv run pytest -q`
Expected: PASS for both (the single-pass tests are unchanged and still green).

- [ ] **Step 5: Commit**

```bash
git add src/genea/cli/transcribecmds.py tests/test_cli_transcribe.py
git commit -m "Add --structure-model to genea transcribe for a double pass"
```

---

### Task 4: Documentation and the manual comparison

**Files:**
- Modify: `README.md` (settings table near line 131, the transcribe section near lines 170-190)
- Modify: `docs/superpowers/specs/2026-10-10-doublepass-transcription-design.md` (status line)

- [ ] **Step 1: Document the setting and the option**

In the README settings table, after the `transcribe_language` row add:

```markdown
| `transcribe_structure_model` | `""` | Small model for the double pass of `genea transcribe` (for example `base`); empty means a single pass |
```

In the transcribe section, after the "Slovak fine-tunes" paragraph, add:

```markdown
**Double pass.** The Slovak fine-tunes write the better words, but the generic models cut segments better, at
the places where the speaker or the sentence changes. `genea transcribe rec.m4a --structure-model base --model
sk-large-v3-turbo --language sk` runs the small model first to find the segment boundaries, then the fine-tune on
the audio of each segment, and writes the first model's times with the second model's text (or set
`transcribe_structure_model` and `transcribe_model`; `--structure-model ""` switches it off again). A segment
for which the fine-tune hears nothing or loops is left out, and a segment whose text is very different in
length from the first pass is kept but listed as `check:` in the output. It takes about one run of the large model
plus a quick run of the small one. Speaker labels are not produced: Whisper does not tell speakers apart.
```

- [ ] **Step 2: Manual comparison (needs the extra and the models; not part of CI)**

On one real recording of about 10 minutes (a copy, with `--out` into the scratchpad directory so the knowledge base is untouched), run:

```bash
uv run genea transcribe REC.m4a --language sk --model large-v3 --out single_large.txt
uv run genea transcribe REC.m4a --language sk --structure-model tiny --model sk-large-v3-turbo --out dp_tiny.txt
uv run genea transcribe REC.m4a --language sk --structure-model base --model sk-large-v3-turbo --out dp_base.txt
```

Compare the segment boundaries of `dp_tiny.txt` and `dp_base.txt` with `single_large.txt` by eye (are turns cut where `large-v3` cuts them?), look for words repeated at neighbouring boundaries (the padding overlap) and for `check:` / `dropped` lines. Report the findings to the user and, from them, pick the value the README recommends. If the overlap duplicates words, propose a fix (smaller padding) in a follow-up, and do not change the constants silently.

- [ ] **Step 3: Mark the spec as implemented**

Change the spec's `Status:` line to `Implemented (see the plan 2026-10-10-doublepass-transcription.md); the manual comparison decides the recommended structure model and the thresholds in section 7`.

- [ ] **Step 4: Run the whole suite and the linter**

Run: `uv run pytest -q` and `uv run ruff check src tests` (if ruff is configured in the project; skip the second command otherwise)
Expected: all pass, no lint errors.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/superpowers/specs/2026-10-10-doublepass-transcription-design.md
git commit -m "Document the double pass"
```
