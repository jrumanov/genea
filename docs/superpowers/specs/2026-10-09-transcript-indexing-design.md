# Transcript indexing and summaries: design

## Goal

Make Whisper transcripts (for example `podcast_family/2022_05_10-fabik_lubica_rozhovor_1.txt`) searchable and
citable by time, and give each recording a summary that helps the agent and the user find the right
recording and passage.

- **A (deterministic):** parse the timestamps, chunk by time, store `start_s`/`end_s` on chunks, cite as
  `[file.txt @14:20]`, let `read_document` read a transcript from a given time.
- **B (LLM):** generate a summary per recording and per section inside `genea index`, cache it outside the
  index, and index it as navigation-only chunks.

Out of scope: speaker diarization, linking spoken names to tree persons beyond the existing name-form
links, any change to the Whisper pipeline.

## Constraints

- Everything stays local (Ollama). `genea index` already needs Ollama for embeddings.
- Summaries are an enhancement: a missing or failing summarizer must never stop a transcript from being
  indexed and searchable.
- A summary is navigation, never evidence. Facts are cited to the transcript timestamp only.
- `.txt` documents that are not transcripts behave exactly as before.
- `--rebuild` and a schema bump must not re-run the LLM for unchanged transcripts.

## Transcript format and detection

Whisper output, one segment per line: `start<TAB>end<TAB> text` (seconds as decimals, text with a leading
space). No speaker labels. A `.txt` file is a transcript when at least 80% of its non-empty lines match that
shape; this is decided by content, not by folder. The first 50 non-empty lines are enough to decide.

## Part A: parsing, chunking, citations

New module `src/genea/ingest/transcript.py`:

- `is_transcript(text) -> bool`.
- `parse_transcript(text) -> list[Segment]` with `Segment(start: float, end: float, text: str)`. Lines that
  do not match are dropped. Runs of 3 or more consecutive segments with identical text (Whisper looping)
  collapse to one.
- `chunk_segments(segments, size=3200, overlap_segments=2) -> list[Chunk]`: a chunk closes when it reaches
  about `size` characters; the cut prefers a pause of more than 3 seconds between segments within the last
  400 characters, otherwise the nearest segment end. Each chunk gets `start_s` of its first segment and
  `end_s` of its last. Chunk text is the segment texts joined by spaces, without timestamps.

`ingest/docs.py`:

- `Chunk` gains `start_s: float | None = None`, `end_s: float | None = None`.
- `extract_document` returns, for a transcript, a `DocExtract` with `segments` set and `pages` empty, so
  existing callers that only know pages are not mixed up. `DocExtract` gains `segments: list[Segment]`
  (default empty) and `is_transcript` derived from it.

Schema (`index/db.py`), `SCHEMA_VERSION` "1" to "2":

- `chunks` gains `start_s REAL`, `end_s REAL`, `kind TEXT NOT NULL DEFAULT 'text'` (`'text'` or
  `'summary'`).
- An old index raises the existing `SchemaMismatch` and asks for `genea index --rebuild`.

Indexing text (`index/indexer.py::_ingest_doc`):

- `chunks.text` holds only the clean transcript text.
- The text given to FTS and to the embedder is a header plus the text. The header is built from the file
  name only: for `2022_05_10-fabik_lubica_rozhovor_1.txt` it is `Nahrávka 2022-05-10 · fabik lubica
  rozhovor 1`. The file name is not split into speaker names, because the surname/given order is
  ambiguous.
- `rebuild_links` keeps using `chunks.text`, so persons are linked from the clean text.

Search and citations:

- `SourceRef` gains `start_s: float | None`. `cite()` returns `[file @mm:ss]` (or `h:mm:ss` from one hour)
  when `start_s` is set and neither `line` nor `page` is.
- `DocHit` gains `start_s`, `end_s` and `kind`. `search_documents` selects the new columns.
- `read_document(path, page=None, start_s=None)`: for a transcript it returns the segments from `start_s`
  (default 0) as lines `[mm:ss] text`, up to `tool_output_chars`, `truncated` set when more follows, and a
  `sources` entry with `start_s`. A `start_s` or `page` argument that does not fit the document type is a
  `ToolError`.
- `agent/core.py` and `mcp_server.py` pass `start_s` through.

## Part B: summaries

New module `src/genea/ingest/summarize.py`:

- `Summarizer` protocol (like `Embedder`): `model: str` and `summarize(segments) -> Summary`.
- `Summary`: `overall: str` and `sections: list[SectionSummary]`; `SectionSummary` has `start_s`, `end_s`,
  `summary`, `topics`, `people`, `places`, `years`.
- `OllamaSummarizer`: map-reduce through Ollama's `/api/chat` with `think` off and JSON output. The
  transcript is split into sections of about 12,000 characters at segment boundaries. Each section yields
  one `SectionSummary`; a final call turns the section summaries into `overall`. The prompt asks for the
  transcript's own language, only what is said, and a marker on uncertain names.
- `FakeSummarizer` for tests: deterministic, offline.
- `make_summarizer(settings)`.

Settings (`config.py`): `summary_model: str = ""` (empty means the model name of `settings.model`, only
for `ollama:` models). `summary_timeout` is not added; a request timeout constant is enough.

Cache (`index/summary_cache.py`):

- File `.genea/summaries/<sha256 of the transcript>.json` with `version`, `model`, `overall`, `sections`.
- `SUMMARY_VERSION` is a module constant. An entry is valid when `version == SUMMARY_VERSION`; the model
  name is recorded for information only, so changing `summary_model` does not trigger regeneration.
- Written atomically (temp file and rename). A corrupt file counts as a miss.

Indexer flow (`index/indexer.py`):

1. `index_kb(..., summarizer: Summarizer | None)`. `None` means summaries are off (`--no-summaries`).
2. A "summarizing" pre-pass runs between the scan and the "indexing" phase. It goes over the pending `.txt`
   files, keeps those that are transcripts with no valid cache entry, and calls the summarizer on each
   (`total` = how many). No database transaction is open during this pass. On success the cache file is
   written; on failure see step 4.
3. `_ingest_doc` for a transcript inserts the text chunks, then, when a cache entry exists, one overall
   summary chunk (`kind='summary'`, `start_s=0`, `end_s` = last segment end) and one per section
   (`kind='summary'`, with the section's time range). Summary text is prefixed `Zhrnutie (generované): `,
   and followed by the section's topics, people, places and years. They are embedded and added to FTS like
   other chunks.
4. If summarizing raises, the file is indexed without summary chunks, `files.status` stays `ok` and
   `files.error_msg` is set to the constant `summary pending: <reason>`. The report lists it as a problem.
5. Skip rule: today a file with an unchanged mtime and status other than `error` is skipped. A file whose
   `error_msg` starts with `summary pending` is not skipped when a summarizer is set, so it is retried on
   the next run. A file indexed under `--no-summaries` is marked `summary pending: skipped` in the same way.
6. `reindex_file` (after an applied change) takes no summarizer and only uses an existing cache entry.

Progress and CLI:

- `progress.phase("summarizing", total=n)` with `current` set to the file path. `IndexProgress` readers
  (`indexcmds.py` bar, `index_status`) treat it as another phase; the bar shows a description for it.
- `genea index --no-summaries` flag. `common.get_summarizer(kb)` mirrors `get_embedder`.

Evidence rule (`agent/prompt.py`), a new rule 7 for both the agent and MCP prompts:

> Search hits marked as summaries (kind `summary`) are generated, not evidence. Use them to decide where to
> look, then read the transcript with `read_document` from that time and cite what it says. Never cite a
> summary.

To enforce this in the tool output, a summary hit's `source` points at the transcript with `start_s` of the
section, so the only citation the agent can copy is the transcript timestamp.

Links: `rebuild_links` already runs over all chunks, so summary chunks get person links too. Consequence:
`find_person` style lookups can surface a recording whose summary names a person. Accepted.

## Details settled while planning

- `read_document` on a transcript also returns `next_start_s` (the start of the first segment that did not fit),
  so a model can continue reading without parsing the text. The window is `tool_output_chars` minus 800
  characters for the JSON around it; otherwise the tool runner would shrink a long window to 2,000 characters.
- When Ollama cannot be reached (`SummaryUnavailable`), the remaining transcripts of the run are not tried;
  they are indexed without summaries and marked `summary pending` with the reason.
- `summary_model` is a plain Ollama model name (no `ollama:` prefix). If it is empty and the chat model is not
  an `ollama:` one, there is no summarizer: `genea index` says so and marks transcripts `summary pending`.
- There is no `--resummarize` flag (decided with the user). Deleting a cache file or bumping `SUMMARY_VERSION`
  takes effect for a transcript when it is next indexed (changed file, `--rebuild`, or `summary pending`).

## Error handling

| Situation | Behaviour |
| --- | --- |
| Ollama down while summarizing | file indexed without summary, marked `summary pending`, retried next run |
| Summarizer returns malformed JSON | same as above |
| Cache file corrupt | treated as a miss, regenerated |
| Embedder fails | unchanged: the file is recorded as an error |
| Transcript with no parsable segments | treated as an ordinary text document |
| Index from schema 1 | `SchemaMismatch`, run `genea index --rebuild` (summary cache is reused) |

## Testing

- `tests/test_transcript.py`: detection, parsing, loop collapse, odd lines, chunk boundaries and time
  ranges, a non-transcript `.txt` unchanged.
- `tests/test_summary_cache.py`: hit, miss, stale `SUMMARY_VERSION`, corrupt file, atomic write.
- `tests/test_indexer.py` additions: transcript chunks carry `start_s`; summary chunks present with the fake
  summarizer; failing summarizer leaves the file searchable and `summary pending`, and the next run retries;
  `--no-summaries` then a normal run fills summaries; `--rebuild` reuses the cache (summarizer call count 0).
- `tests/test_index_db.py`: schema version 2, old index raises `SchemaMismatch`.
- `tests/test_search.py`: hits carry `start_s`, `kind`; the summary hit's source cites the transcript time;
  `SourceRef.cite()` formats `@mm:ss`.
- `tests/test_docs.py` or a new file: `read_document` with `start_s`, truncation, wrong-argument errors.
- `tests/test_cli_index.py`: `--no-summaries`, "summarizing" phase.
- Prompt tests: rule 7 present in both prompts.
- One optional live test in `tests/live` runs the Ollama summarizer on a short transcript.
- Fixtures: a small synthetic Whisper transcript in `tests/fixtures/make_kb.py`.

## Open items to decide during implementation, with defaults

- Section size (12,000 characters) and pause threshold (3 s) are constants, tuned on the user's three
  real transcripts without committing their content.
- README gets a short "Transcripts" section describing the format, `--no-summaries` and `summary_model`.
