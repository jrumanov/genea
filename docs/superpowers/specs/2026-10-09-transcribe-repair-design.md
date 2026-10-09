# Transcribing and repairing recordings — Design

Date: 2026-10-09
Status: Draft, awaiting review (revised after first review: .orig safety, splice start, early ends, check without av, UTF-8, model names)

## 1. Goal

Whisper transcripts sometimes fail partway: the decoder loops on one line to the end of the
recording (`2023_07_30-fabik_anna_2.txt`: real speech ends at 17:26, then "No a ja tam si sa učila."
repeats until 40:01). The parser already collapses such runs, so the index silently holds a short,
incomplete recording. This adds CLI commands to transcribe audio with anti-loop settings, to find
looped transcripts, and to re-transcribe only the lost part and splice it in.

### Decisions

| Topic | Decision |
|---|---|
| Engine | whisper.cpp through `pywhispercpp`; optional extra `transcribe` (`pywhispercpp`, `av`) |
| Model | Setting `transcribe_model`, default `"medium"`: a model name (downloaded on first use and cached) or a path to a ggml `.bin` (e.g. Audacity's). `--model` overrides it per run (e.g. `large-v3`). The names `pywhispercpp` accepts are checked in the first implementation task; this spec does not list them |
| Detection | At read time from the transcript files. No index change, no schema bump, no KB rebuild |
| Interface | CLI only. MCP stays read-only and unchanged |
| Language | Setting `transcribe_language`, default empty = auto-detect; a code such as `sk` fixes it. `--language L` overrides it per run. The user's KB config sets `sk` |
| Console | All three commands force UTF-8 on stdout, so Slovak text survives pipes and Windows code pages |

### Non-goals

Anything in MCP; speaker labels; translation; GPU/OpenVINO acceleration; transcribing a whole
folder in one command; running the fix-transcripts review automatically after a repair.

## 2. Commands

| Command | Behaviour |
|---|---|
| `genea transcribe <audio> [--model M] [--language L] [--start S] [--out F]` | Writes a Whisper-format `.txt` (`start<TAB>end<TAB> text`, CRLF) for the audio, or for the part from second S. Default output: next to the audio, same stem, `.txt`; refuses to overwrite an existing file |
| `genea transcripts check [path]` | Scans the KB's transcripts (or one file) and lists lost spans (dead spans and early-ending transcripts, section 3). `genea doctor` shows a one-line summary |
| `genea transcripts repair <transcript> [--from S] [--model M] [--language L] [--no-splice]` | Re-transcribes each lost span (or from S) and splices the result in |

**Live progress.** While a command runs it prints one line per decoded segment, as soon as that
segment is decoded (not at the end), for example:

```
[ 98.1%] 39:35  elapsed 11.0m  eta 0.2m  To sú kokosové.
```

- `%`: share of the audio being transcribed that is done, to one decimal. For a span or `--start`, it counts only that part.
- Time: the segment's end on the recording's own timeline (`m:ss`, `h:mm:ss` from one hour), so it matches the transcript file and not the position inside a cut.
- `elapsed`: wall time since the run started. `eta`: elapsed time divided by the share done, minus elapsed; shown as `eta ?` until 1% is done.
- Text: the decoded segment in full, as written to the file.
- The file is flushed after every segment, so it can be opened or tailed during the run.
- At the end one summary line: segments written, total elapsed time, output path.

`progress.py` owns this format, so the three commands print it identically.

## 3. Detection

`ingest/transcript.py` gets one helper that finds runs of identical consecutive segments. The
parser's collapsing and the new check both use it, so they cannot disagree; the parser's output
does not change.

A **dead span** is a run of at least `LOOP_RUN` (3) identical segments lasting at least
`DEAD_SPAN_SECONDS` (60 s). Shorter runs are repetition in speech and are not reported.

An **early end** is a transcript whose last segment ends at least `DEAD_SPAN_SECONDS` before the end
of its audio, with no loop: the transcript simply stops. Both kinds are **lost spans**: a dead span
runs from its start to its end (or to the end of the audio when it is the tail); an early end runs
from the last segment to the end of the audio.

`check` reports per transcript: each lost span (start, end, and the repeated text for a dead span),
the audio length read from the sibling audio file when there is one, and how much of the recording
has real text (for the example: 0:00–17:25 of 40:01). `check` works without the `transcribe` extra:
without `av` it cannot read the audio length, so it reports dead spans only and says that early
ends were not checked.

## 4. Repair

1. Check the backup: if `<name>.txt.orig` does not exist, create it from the current file
   before anything else and say so; stop if it cannot be written. The backup is never overwritten.
2. Find the audio next to the transcript: same stem with `.m4a .mp3 .wav .mp4 .ogg .flac`. None:
   stop with a clear message.
3. For each lost span, cut the audio from the start of the segment before the span (so a sentence
   is not cut in half; the new text wins from there on) to the end of the span, or to the end of
   the audio for a tail. Convert it to 16 kHz mono wav and transcribe it with anti-loop settings
   (language as in section 5; no context carried between windows). Shift timestamps back to the recording's
   timeline.
4. Write the new segments to `<kb>/.genea/work/<stem>.retranscribe.txt`. With `--no-splice`, stop.
5. Splice: replace the segments from the preceding segment through the end of the span with the new
   ones. Every other line stays byte-identical, CRLF included. Write to a temp file, then rename.
   Refuse to write unless timestamps stay in order and all untouched lines are unchanged.
6. Print what was replaced and remind the user to run `genea index` (the file's hash changes, so
   only this transcript is re-indexed and its cached summary regenerated).

**New output that loops again.** If the re-transcribed span itself contains a dead span, that part
is not spliced. The command reports "still unreadable at m:ss–m:ss" and leaves the original loop in
place.

## 5. Language and model

**Language.** `--language` wins over `transcribe_language`; if neither is set, whisper.cpp detects the
language itself. Each run prints the language used, and the detected one when it was auto-detected.
Detection on a short or noisy span can pick the wrong language, so for a repair the safe practice is
to set `transcribe_language` in the KB config; `repair` with auto-detection says so when it runs.

**Model.** 
`transcribe_model` is resolved as: an existing file path is used as is; otherwise it is a model
name (default `medium`; others such as `large-v3` as far as `pywhispercpp` supports them) that it
downloads into its cache on first use. Before a download, print the model name and approximate size. A failed
download names the model, the cache folder and the option of setting a local `.bin` path.

## 6. Errors

| Situation | Behaviour |
|---|---|
| `transcribe` extra not installed | Print `uv sync --extra transcribe` |
| No audio next to the transcript | Stop, name the folders and extensions searched |
| Ctrl+C during a run | Partial output stays in `.genea/work/`; the transcript is untouched |
| Output file already exists (`transcribe`) | Refuse; the user passes `--out` |
| `.orig` missing at repair time | Create it from the current file first, or stop if that fails |
| `check` without `av` | Report dead spans only; say that early ends were not checked |

## 7. Code layout

- `src/genea/transcribe/engine.py`: whisper.cpp wrapper, model resolution and download, progress callback
- `src/genea/transcribe/audio.py`: find the sibling audio, cut a span to wav
- `src/genea/transcribe/repair.py`: dead spans, re-transcribe, splice
- `src/genea/transcribe/progress.py`: the live progress line (section 2)
- `src/genea/ingest/transcript.py`: shared run-finding helper
- `src/genea/cli/transcribe.py`: the three commands; `doctor.py`: loop line
- `src/genea/config.py`: `transcribe_model`, `transcribe_language`; `pyproject.toml`: extra `transcribe`
- README: a section next to "Transcripts" and rows in the commands table

## 8. Testing

Unit tests, with a fake transcriber injected so no model is needed: dead-span detection (short
stutters not flagged, mid-file and multiple loops), splice and its safety checks (byte-identical
untouched lines, timestamp order, `.orig` kept once and created when missing, the preceding segment replaced), early-end detection, `check` without `av`, a re-looping output left unspliced, UTF-8 stdout, model
resolution (path vs. name), language resolution (flag, then setting, then auto), the progress line (percent, timeline time, eta before 1% and at the end), and config. One live test with the real whisper.cpp, skipped unless a
model and an audio file are available, goes next to the existing live tests.
