# Double-pass transcription — Design

Date: 2026-10-10
Status: Implemented (see the plan 2026-10-10-doublepass-transcription.md); the manual comparison decides the recommended structure model and the thresholds in section 7

## 1. Goal

The Slovak fine-tuned Whisper models (`sk-*`) give more accurate words, but their segment
boundaries are worse than those of the generic models: segments are cut at the wrong places and
turns of different speakers run together. Whisper does no speaker diarization, so "better at
splitting speakers" means segment boundaries that fall where the speaker or sentence changes.

`genea transcribe` gets an optional second pass: a small generic model finds the segment
boundaries quickly, then the accurate model re-transcribes each segment's audio. The result keeps
the first model's boundaries and the second model's text.

### Decisions

| Topic | Decision |
|---|---|
| Pass 1 (structure) | A small generic model, set by `--structure-model` or the new setting `transcribe_structure_model`. Suggested value `base`; `tiny` is faster. Empty (the default) = no double pass, `genea transcribe` behaves exactly as today |
| Pass 2 (text) | The existing `--model` / `transcribe_model`. For this mode the user sets it to `sk-large-v3-turbo` |
| Output | Segments with pass 1's start and end and pass 2's text, in the same Whisper-format `.txt` through the existing `.part` file and rename. Indexing, `transcripts check` and repair need no change |
| Memory | One model in memory at a time: pass 1 finishes and its model is released before pass 2 loads |
| Interface | CLI only. MCP stays read-only and unchanged |

### Non-goals

Speaker labels (diarization); word-level alignment; double pass in `transcripts repair`
(repair keeps using one model); transcribing a folder in one command; any index or schema change.

## 2. Command

`genea transcribe <audio> [--model M] [--structure-model S] [--language L] [--start S] [--out F]`

`--structure-model S` turns the double pass on for this run (overriding the setting); an empty
value (`--structure-model ""`) turns it off when the setting is set. All other options and the
refusal to overwrite an existing file are unchanged.

**Live progress.** Pass 1 prints one line per decoded segment as today, prefixed `[1/2 structure]`.
Pass 2 prints one line per finished segment, prefixed `[2/2 text]`, with the progress bar based on
the audio position. Only pass 2 writes to the `.part` file, so an interrupted run leaves text
that is already final, never boundaries without text.

## 3. Algorithm

1. **Pass 1.** Transcribe the whole recording with the structure model. Collect the segments
   `(start, end, text)`. Discard its text, except as described in step 4.
2. **Clean the boundaries.**
   - Drop segments whose text is empty after stripping, and runs of identical text (3 or more
     identical segments in a row, found by `find_runs` in `genea.ingest.transcript`). These are silence hallucinations of
     the small model.
   - Merge a segment shorter than 1.0 s into its neighbour (the previous one, or the next one for
     the first segment), because Whisper hallucinates on very short clips.
3. **Pass 2.** Load the text model. For each cleaned segment, cut the span
   `[start - 0.3, end + 0.3]` seconds (clamped to the recording) from the samples already in
   memory (slicing; `load_span` is not called again) and transcribe it with `no_context=True`. Join the produced segments' text with a space. Emit
   `Segment(start, end, text)` with pass 1's times.
4. **Fallbacks per segment.**
   - Pass 2 returns no text, or text that repeats (the loop test above): the segment is dropped.
     Pass 1's text is *not* used in that case, because the same audio already made the small
     model produce it.
   - Pass 2's text is more than 4 times longer than pass 1's text, or less than a quarter of its
     length (compared in characters, only when pass 1's text has at least 20 characters): keep pass 2's
     text but print a `check:` line in the progress output naming the segment, so the user can
     review it. Nothing is silently changed.
5. **Language.** The language is resolved once (`resolve_language`) and passed to both passes.
   With auto-detect, pass 1 detects it and the detected code is passed to pass 2. If the language
   could not be detected and none is set, a hint to pass `--language` is printed and pass 2 goes on
   with auto-detect.

## 4. Components

| Unit | Responsibility | Depends on |
|---|---|---|
| `genea.transcribe.doublepass` (new) | `double_pass(samples, language, make_structure, make_text, on_segment, *, say, on_structure, on_text_start)`: steps 1 to 4 of the algorithm over two injected transcriber factories, so the first model is released before the second is made | `Segment`, `find_runs` from `genea.ingest.transcript` |
| `genea.transcribe.engine` | Unchanged; `WhisperCpp` is created once per pass | |
| `genea.cli.transcribecmds` | New option, setting lookup, progress prefixes, calls `double_pass` when a structure model is set | `doublepass`, `make_transcriber` |
| `genea.config` | New setting `transcribe_structure_model: str = ""` | |
| README | Document the option and the recommended pair (`base` + `sk-large-v3-turbo`) | |

`double_pass` takes the two transcribers as factories (the same `Transcriber` callable type as
today), so it is tested without loading any model. It calls the first factory, runs pass 1,
drops the model, and only then calls the second factory.

## 5. Errors

- Unknown or unavailable model for either pass: the same `ModelUnavailable` messages as today,
  raised before any audio is decoded. The structure model is resolved first, then the text model,
  so a typo in either name fails fast, before pass 1 starts.
- Pass 1 finds no usable segments: stop with a clear message ("the structure model found no
  speech") and write nothing.
- Interrupted (Ctrl+C): as today, `.part` keeps the finished segments, the real file is not written.

## 6. Testing

Unit tests, no models loaded (fake transcribers):

- Boundaries come from pass 1, text from pass 2.
- Segments under 1.0 s are merged; the first segment merges forward.
- Padding is applied and clamped at 0 and at the recording end.
- Pass 2 empty or looping text: the segment is dropped, pass 1's text is not used.
- Length-ratio mismatch: pass 2's text is kept and a `check:` line is printed.
- Looping pass 1 output is cleaned before pass 2 runs.
- CLI: without `--structure-model` and without the setting, behaviour and output are identical to
  today; with it, both models are resolved before audio is decoded; the setting works and
  `--structure-model ""` turns it off; the `.part` file holds only pass 2 segments.

Manual (not part of CI): run `tiny` and `base` as the structure model on one real recording with
`sk-large-v3-turbo`, compare the boundaries against a single-pass generic run, and set the
documented recommendation from the result.

## 7. Open points

- Whether `tiny` is good enough for Slovak boundaries is decided by the manual comparison above.
- The 1.0 s merge threshold, 0.3 s padding and 4x length ratio are starting values, adjusted after
  the manual comparison.
