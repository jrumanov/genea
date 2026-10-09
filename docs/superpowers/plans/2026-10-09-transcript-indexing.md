# Transcript Indexing and Summaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Whisper transcripts searchable and citable by time (`[file.txt @14:20]`) and give each recording a cached, navigation-only summary generated inside `genea index`.

**Architecture:** A new `ingest/transcript.py` parses `start<TAB>end<TAB> text` lines into timed segments; `ingest/docs.py` chunks them by time; chunks gain `start_s`/`end_s`/`kind` (schema 2). A `Summarizer` (Ollama, plus a fake for tests) writes summaries to `.genea/summaries/<sha256>.json` in a pre-pass before the indexing transaction; the indexer adds them as `kind='summary'` chunks. Search results and `read_document` carry timestamps.

**Tech Stack:** Python 3.12, SQLite (FTS5 + sqlite-vec), httpx (Ollama `/api/chat`), PydanticAI/MCP tool wrappers, pytest. Run tests with `uv run pytest`.

**Spec:** `docs/superpowers/specs/2026-10-09-transcript-indexing-design.md`

## Global Constraints

- Everything stays local (Ollama); no new dependencies.
- A missing or failing summarizer must never stop a transcript from being indexed and searchable.
- A summary is navigation, never evidence: facts are cited to the transcript timestamp only.
- `.txt` documents that are not transcripts behave exactly as before (page `NULL`, no timestamps).
- `--rebuild` and a schema bump must not re-run the LLM for an unchanged transcript.
- `SCHEMA_VERSION` goes from `"1"` to `"2"`; an old index raises `SchemaMismatch` telling the user to run `genea index --rebuild`.
- Summary cache file: `.genea/summaries/<sha256 of the transcript>.json` holding `version`, `model`, `overall`, `sections`; valid only when `version == SUMMARY_VERSION`; written atomically.
- Citation format for transcripts: `[path @m:ss]` (or `[path @h:mm:ss]` from one hour).
- Test count assertions in existing tests (`ok 5`, `skipped == 6`, ...) must keep passing: do **not** add a transcript to `build_sample_kb`; write transcripts inside the new tests.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

Inputs and failures the spec implies but a user would hit; each has a test in the named task. Items 6 and 7 came from the second review.

1. Ollama is down while 45 transcripts are pending: the run must stop after the first connection failure, not wait out 45 timeouts, and every transcript must still be searchable (Task 7).
2. A long transcript, or any long document, read through `run_tool` must not be shrunk to 2,000 characters by `render()`; the text has to fit `tool_output_chars` measured as escaped JSON, with room for the other fields. Today an ordinary 32,000-character `.txt` reaches the model as 2,001 characters (measured); Task 4 fixes that too (Task 4).
6. Generated summary text must never become evidence: no person links from summary chunks, so `find_conflicts` cannot report a "document mismatch" from a year the LLM wrote (Task 7).
7. Ollama's default context window can be smaller than a 12,000-character section; the request must set `num_ctx`, or the model silently sees only the start of the section (Task 5).
3. `start_s = 0` is falsy: a hit or a read at the very start must cite `@0:00`, not drop the timestamp (Task 4).
4. A transcript saved by Windows tools with CRLF line endings (and possibly a UTF-8 BOM) must parse like any other (Tasks 1 and 2).
5. A summarizer that returns malformed JSON, or a cache file that is corrupt or from an older `SUMMARY_VERSION`, must degrade to "no summary yet" and never raise out of `genea index` (Tasks 5, 6 and 7).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/genea/ingest/transcript.py` (new) | `Segment`, `is_transcript`, `parse_transcript`, `format_time`, `transcript_header`. No genea imports. |
| `src/genea/ingest/docs.py` | `Chunk` gains times/kind; `DocExtract.segments`; `chunk_segments`; `summary_chunks`; `read_transcript`. |
| `src/genea/ingest/summarize.py` (new) | `Summary` types, `split_sections`, `Summarizer` protocol, `FakeSummarizer`, `OllamaSummarizer`, `make_summarizer`, error classes. |
| `src/genea/index/summary_cache.py` (new) | `SUMMARY_VERSION`, `load`, `store`. |
| `src/genea/index/db.py` | Schema 2: `chunks.start_s/end_s/kind`. |
| `src/genea/index/indexer.py` | Transcript ingest, summary chunks, `summarizer` parameter, pre-pass, pending marker and skip rule. |
| `src/genea/index/progress.py` | `summarizing` phase in `describe` and `eta_seconds`. |
| `src/genea/kb.py`, `src/genea/config.py` | `summaries_dir`; `summary_model` setting. |
| `src/genea/tools/models.py`, `tools/search.py` | `start_s` on `SourceRef`/`DocHit`/`DocumentText`; `read_document` transcript windows. |
| `src/genea/agent/core.py`, `mcp_server.py`, `agent/prompt.py` | `start_s` argument; rule about summary hits. |
| `src/genea/cli/indexcmds.py`, `cli/common.py` | `--no-summaries`, summarizing progress, `get_summarizer`. |
| `tests/fixtures/transcripts.py` (new) | Whisper-format test data. |
| `tests/test_transcript.py`, `test_summarize.py`, `test_summary_cache.py`, `test_transcript_indexing.py` (new) | Tests per module. |

---

### Task 1: Transcript parsing

**Files:**
- Create: `src/genea/ingest/transcript.py`
- Create: `tests/fixtures/transcripts.py`
- Create: `tests/test_transcript.py`

**Interfaces:**
- Produces: `Segment(start: float, end: float, text: str)` (dataclass); `is_transcript(text: str) -> bool`; `parse_transcript(text: str) -> list[Segment]` (`[]` when the text is not a transcript); `format_time(seconds: float) -> str`; `transcript_header(stem: str) -> str`. Test helpers `whisper(rows) -> str`, `sample_rows(count=60)`, `write_transcript(root, name=NAME, rows=None) -> Path`, constant `NAME`.

- [ ] **Step 1: Write the test helpers**

Create `tests/fixtures/transcripts.py`:

```python
"""Whisper-style transcripts for tests."""

from __future__ import annotations

from pathlib import Path

NAME = "2022_05_10-fabik_lubica_rozhovor_1.txt"


def whisper(rows) -> str:
    """Whisper output as the real files have it: start, end and ' text', separated by tabs."""
    return "".join(f"{start:.6f}\t{end:.6f}\t {text}\n" for start, end, text in rows)


def sample_rows(count: int = 60) -> list[tuple[float, float, str]]:
    """`count` segments, one every 15 s; the one at 14:00 (840 s) mentions Prískupice."""
    rows = [(i * 15.0, i * 15.0 + 14.0,
             f"Veta číslo {i} o rodine a dome na dedine, ktorú si pamätám z detstva a z rozprávania starých rodičov.")
            for i in range(count)]
    rows[56] = (840.0, 854.0, "V roku 1954 sa zobrali a nasťahovali sa do Prískupíc k babke.")
    return rows


def write_transcript(root: Path, name: str = NAME, rows=None) -> Path:
    path = root / "docs" / name
    path.write_text(whisper(sample_rows() if rows is None else rows), encoding="utf-8")
    return path
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_transcript.py`:

```python
import pytest
from fixtures.transcripts import whisper

from genea.ingest.transcript import Segment, format_time, is_transcript, parse_transcript, transcript_header


def test_detects_whisper_output():
    assert is_transcript(whisper([(0.6, 8.56, "Dobre, takže podcast."), (8.56, 10.56, "Čo mám hovoriť?")]))


@pytest.mark.parametrize("text", ["", "   \n\n", "Kronika obce Brno.\n\nJosef Novák, sedlák.\n",
                                  "1845\tBrno\n1846\tPraha\n"])
def test_ordinary_text_is_not_a_transcript(text):
    assert not is_transcript(text)


def test_share_of_matching_lines_decides():
    rows = whisper([(i, i + 1, "x") for i in range(4)])
    assert is_transcript(rows + "a stray line\n")  # 4 of 5 lines
    assert not is_transcript(rows + "a stray line\nand another\n")  # 4 of 6 lines


def test_parse_strips_text_and_skips_blank_and_stray_lines():
    text = whisper([(0.6, 8.56, "Dobre."), (8.56, 9.0, "   "), (9.0, 12.5, "Čo mám hovoriť?"),
                    (12.5, 14.0, "Áno."), (14.0, 15.0, "Nie.")]) + "stray\n"  # 5 of 6 lines still count
    assert parse_transcript(text) == [Segment(0.6, 8.56, "Dobre."), Segment(9.0, 12.5, "Čo mám hovoriť?"),
                                      Segment(12.5, 14.0, "Áno."), Segment(14.0, 15.0, "Nie.")]


def test_parse_of_ordinary_text_is_empty():
    assert parse_transcript("Kronika obce Brno.\n") == []


def test_windows_line_endings():
    text = whisper([(0, 2, "Dobre."), (2, 4, "Čo mám hovoriť?")]).replace("\n", "\r\n")
    assert [s.text for s in parse_transcript(text)] == ["Dobre.", "Čo mám hovoriť?"]


def test_whisper_loops_collapse_to_one_segment():
    text = whisper([(0, 2, "Dobre."), (2, 4, "Ďakujem."), (4, 6, "Ďakujem."), (6, 8, "Ďakujem."),
                    (8, 10, "Ďakujem."), (10, 12, "Hotovo.")])
    assert parse_transcript(text) == [Segment(0, 2, "Dobre."), Segment(2, 10, "Ďakujem."), Segment(10, 12, "Hotovo.")]


def test_two_repeats_are_kept():
    assert len(parse_transcript(whisper([(0, 2, "Áno."), (2, 4, "Áno."), (4, 6, "Nie.")]))) == 3


@pytest.mark.parametrize(("seconds", "shown"), [(0, "0:00"), (9.9, "0:09"), (840, "14:00"), (3599, "59:59"),
                                                (3725, "1:02:05")])
def test_format_time(seconds, shown):
    assert format_time(seconds) == shown


def test_header_from_file_name():
    assert transcript_header("2022_05_10-fabik_lubica_rozhovor_1") == "Nahrávka 2022-05-10 · fabik lubica rozhovor 1"
    assert transcript_header("rozhovor_s_babkou") == "Nahrávka · rozhovor s babkou"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_transcript.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.ingest.transcript'`

- [ ] **Step 4: Write the implementation**

Create `src/genea/ingest/transcript.py`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_transcript.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add src/genea/ingest/transcript.py tests/fixtures/transcripts.py tests/test_transcript.py
git commit -m "$(cat <<'EOF'
Parse Whisper transcripts into timed segments

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Time-based chunking

**Files:**
- Modify: `src/genea/ingest/docs.py`
- Modify: `tests/test_transcript.py` (append)
- Modify: `tests/test_docs.py` (append)

**Interfaces:**
- Consumes: `Segment`, `parse_transcript` from Task 1.
- Produces: `Chunk(page, chunk_no, text, start_s=None, end_s=None, kind="text")`; `DocExtract.segments: list[Segment]`; `chunk_segments(segments, size=3200, overlap_segments=2, pause=3.0) -> list[Chunk]`; `read_transcript(path: Path) -> list[Segment]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transcript.py` (add `from genea.ingest.docs import chunk_segments` to the imports):

```python
def _segments(count, text_len=100, gaps=None):
    """`count` two-second segments of `text_len` characters; gaps={i: seconds of silence before segment i}."""
    gaps = gaps or {}
    out, clock = [], 0.0
    for i in range(count):
        clock += gaps.get(i, 0.0)
        out.append(Segment(clock, clock + 2.0, f"{i:03d}" + "x" * (text_len - 3)))
        clock += 2.0
    return out


def test_chunks_carry_times_and_clean_text():
    segments = _segments(30)
    chunks = chunk_segments(segments, size=1000, overlap_segments=2)
    assert [c.chunk_no for c in chunks] == list(range(len(chunks)))
    assert chunks[0].start_s == segments[0].start
    assert chunks[-1].end_s == segments[-1].end
    assert all(c.page is None and c.kind == "text" for c in chunks)
    assert "\t" not in chunks[0].text and chunks[0].text.startswith("000xxx")


def test_chunk_prefers_a_long_pause_near_the_size_limit():
    segments = _segments(30, gaps={8: 5.0})  # 5 s of silence before segment 8
    chunks = chunk_segments(segments, size=1000, overlap_segments=2)
    assert chunks[0].end_s == segments[7].end
    assert chunks[1].start_s == segments[6].start  # two segments of overlap


def test_chunk_without_a_pause_closes_at_the_size_limit():
    segments = _segments(30)
    chunks = chunk_segments(segments, size=1000, overlap_segments=0)
    assert chunks[0].end_s == segments[9].end  # ten 100-character segments reach 1000
    assert chunks[1].start_s == segments[10].start


def test_every_segment_is_in_some_chunk():
    segments = _segments(55)
    joined = " ".join(c.text for c in chunk_segments(segments, size=1000))
    assert all(s.text in joined for s in segments)


def test_a_segment_longer_than_the_chunk_size_still_terminates():
    segments = [Segment(0, 5, "y" * 5000), Segment(5, 6, "short")]
    chunks = chunk_segments(segments, size=1000)
    assert [c.text for c in chunks] == ["y" * 5000, "short"]


def test_no_segments_no_chunks():
    assert chunk_segments([]) == []
```

Append to `tests/test_docs.py`:

```python
def test_whisper_transcript_has_segments_not_pages(tmp_path):
    path = tmp_path / "2022_05_10-a_b_rozhovor.txt"
    path.write_bytes("﻿0.600000\t8.560000\t Dobre.\r\n8.560000\t10.0\t Čo mám hovoriť?\r\n".encode("utf-8"))
    extract = extract_document(path)
    assert extract.pages == [] and not extract.needs_ocr
    assert [s.text for s in extract.segments] == ["Dobre.", "Čo mám hovoriť?"]


def test_ordinary_text_has_no_segments(sample_root):
    assert extract_document(sample_root / "docs" / "kronika.txt").segments == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_transcript.py tests/test_docs.py -v`
Expected: FAIL (`ImportError: cannot import name 'chunk_segments'`; `AttributeError: ... 'segments'`)

- [ ] **Step 3: Write the implementation**

In `src/genea/ingest/docs.py`, add the import below the pypdf import:

```python
from genea.ingest.transcript import Segment, parse_transcript
```

Replace the `DocExtract` and `Chunk` dataclasses with:

```python
@dataclass
class DocExtract:
    pages: list[Page] = field(default_factory=list)
    needs_ocr: bool = False
    segments: list[Segment] = field(default_factory=list)  # a Whisper transcript: timed segments, no pages


@dataclass
class Chunk:
    page: int | None
    chunk_no: int
    text: str
    start_s: float | None = None
    end_s: float | None = None
    kind: str = "text"  # 'summary' for generated summaries
```

Replace the first branch of `extract_document`:

```python
    if suffix == ".txt":
        text = _read_text_file(path)
        segments = parse_transcript(text)
        return DocExtract(segments=segments) if segments else _single(text)
    if suffix == ".md":
        return _single(_read_text_file(path))
```

Append to the end of the file:

```python
def read_transcript(path: Path) -> list[Segment]:
    """Segments of a Whisper transcript file; [] for any other file."""
    return parse_transcript(_read_text_file(path)) if path.suffix.lower() == ".txt" else []


PAUSE_SECONDS = 3.0  # silence that makes a good place to cut a chunk
PAUSE_WINDOW = 400  # a pause is used for the cut when it comes within this many characters of the size limit


def chunk_segments(segments: list[Segment], size: int = 3200, overlap_segments: int = 2,
                   pause: float = PAUSE_SECONDS) -> list[Chunk]:
    """Chunks of ~`size` characters. A cut prefers a pause of more than `pause` seconds within the last
    PAUSE_WINDOW characters; the next chunk repeats the last `overlap_segments` segments."""
    chunks: list[Chunk] = []
    n = len(segments)
    start = 0
    while start < n:
        length, end, cut = 0, start, None
        while end < n and length < size:
            if end > start and length >= size - PAUSE_WINDOW and segments[end].start - segments[end - 1].end > pause:
                cut = end
            length += len(segments[end].text) + 1
            end += 1
        if end < n and cut is not None:
            end = cut
        piece = segments[start:end]
        chunks.append(Chunk(None, len(chunks), " ".join(s.text for s in piece), piece[0].start, piece[-1].end))
        if end >= n:
            break
        start = max(end - overlap_segments, start + 1)
    return chunks
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_transcript.py tests/test_docs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/genea/ingest/docs.py tests/test_transcript.py tests/test_docs.py
git commit -m "$(cat <<'EOF'
Chunk transcripts by time, with start and end seconds

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Schema 2 and transcript ingest

**Files:**
- Modify: `src/genea/index/db.py` (`SCHEMA_VERSION`, `chunks` table)
- Modify: `src/genea/index/indexer.py` (`_ingest_doc`, imports)
- Modify: `tests/test_index_db.py`
- Create: `tests/test_transcript_indexing.py`

**Interfaces:**
- Consumes: `chunk_segments`, `DocExtract.segments` (Task 2); `transcript_header` (Task 1); `write_transcript`, `NAME` (Task 1).
- Produces: `chunks` rows with `start_s`, `end_s`, `kind`; transcript chunks' FTS/embedding input is `"{header}\n{text}"` while `chunks.text` is the clean text.

- [ ] **Step 1: Write the failing tests**

In `tests/test_index_db.py` change the expectation on line 15 to `"schema_version": "2"` and append:

```python
def test_index_from_schema_1_needs_a_rebuild(tmp_path):
    path = tmp_path / "i.db"
    conn = connect(path, "fake", 8)
    conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaMismatch, match="--rebuild"):
        connect(path, "fake", 8)
```

Create `tests/test_transcript_indexing.py`:

```python
import pytest
from fixtures.transcripts import NAME, write_transcript

from genea.index.db import open_index
from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb

REL = f"docs/{NAME}"


def _query(kb, sql, params=()):
    conn = open_index(kb, FakeEmbedder())
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _chunks(kb, rel=REL, kind="text"):
    return _query(kb, "SELECT c.* FROM chunks c JOIN files f ON f.id = c.file_id "
                      "WHERE f.path = ? AND c.kind = ? ORDER BY c.chunk_no", (rel, kind))


def test_transcript_chunks_carry_times_and_clean_text(kb):
    write_transcript(kb.root)
    index_kb(kb, FakeEmbedder())
    chunks = _chunks(kb)
    assert len(chunks) >= 2
    assert all(c["start_s"] is not None and c["end_s"] > c["start_s"] and c["page"] is None for c in chunks)
    assert [c["start_s"] for c in chunks] == sorted(c["start_s"] for c in chunks)
    assert all("\t" not in c["text"] and "fabik" not in c["text"] for c in chunks)
    last = max(chunks, key=lambda c: c["start_s"])
    assert last["start_s"] <= 840 <= last["end_s"]
    assert "Prískupíc" in last["text"]


def test_file_name_context_is_searchable_but_not_stored(kb):
    write_transcript(kb.root)
    index_kb(kb, FakeEmbedder())
    ids = {c["id"] for c in _chunks(kb)}
    hits = {r[0] for r in _query(kb, "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?", ('"fabik"',))}
    assert hits == ids  # every chunk of this recording, and nothing else
    assert _query(kb, "SELECT COUNT(*) FROM chunks_fts")[0][0] == _query(kb, "SELECT COUNT(*) FROM chunks")[0][0]


def test_ordinary_text_has_no_times(kb):
    index_kb(kb, FakeEmbedder())
    rows = _chunks(kb, "docs/kronika.txt")
    assert rows and all(r["start_s"] is None and r["end_s"] is None for r in rows)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_index_db.py tests/test_transcript_indexing.py -v`
Expected: FAIL (schema_version `'1'`; `no such column: c.kind` / `start_s`)

- [ ] **Step 3: Write the implementation**

In `src/genea/index/db.py` set `SCHEMA_VERSION = "2"` and replace the `chunks` table:

```sql
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    page INTEGER,
    chunk_no INTEGER NOT NULL,
    text TEXT NOT NULL,
    start_s REAL,
    end_s REAL,
    kind TEXT NOT NULL DEFAULT 'text'
);
```

In `src/genea/index/indexer.py` change the docs import to
`from genea.ingest.docs import DOC_EXTS, chunk_pages, chunk_segments, extract_document`, add
`from genea.ingest.transcript import transcript_header`, and replace `_ingest_doc`:

```python
def _ingest_doc(conn, kb, embedder, path, file_id) -> tuple[str, str | None]:
    extract = extract_document(path)
    if extract.needs_ocr:
        return "needs_ocr", "no text layer; OCR is needed"
    if extract.segments:  # (chunk, text for FTS and the embedder): the recording's name adds searchable context
        header = transcript_header(path.stem)
        items = [(c, f"{header}\n{c.text}") for c in chunk_segments(extract.segments)]
    else:
        items = [(c, c.text) for c in chunk_pages(extract.pages)]
    vectors: list[list[float]] = []
    for i in range(0, len(items), EMBED_BATCH):
        vectors += embedder.embed([indexed for _, indexed in items[i:i + EMBED_BATCH]])
    for (chunk, indexed), vector in zip(items, vectors):
        chunk_id = conn.execute(
            "INSERT INTO chunks (file_id, page, chunk_no, text, start_s, end_s, kind) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (file_id, chunk.page, chunk.chunk_no, chunk.text, chunk.start_s, chunk.end_s, chunk.kind),
        ).lastrowid
        conn.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (chunk_id, indexed))
        conn.execute("INSERT INTO chunks_vec (chunk_id, embedding) VALUES (?, ?)",
                     (chunk_id, sqlite_vec.serialize_float32(vector)))
    return "ok", None
```

- [ ] **Step 4: Run the whole suite to verify it passes**

Run: `uv run pytest -q`
Expected: PASS (the new tests and every existing test)

- [ ] **Step 5: Commit**

```bash
git add src/genea/index/db.py src/genea/index/indexer.py tests/test_index_db.py tests/test_transcript_indexing.py
git commit -m "$(cat <<'EOF'
Index transcript chunks with times (schema 2)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Timestamp citations, search hits and reading from a time

**Files:**
- Modify: `src/genea/tools/models.py` (`SourceRef`, `DocHit`, `DocumentText`)
- Modify: `src/genea/tools/search.py` (`search_documents`, `read_document`, text budget for all documents)
- Modify: `src/genea/agent/core.py`, `src/genea/mcp_server.py` (`read_document` wrappers)
- Modify: `tests/test_mcp_server.py` (read_document case), `tests/test_search.py` (`test_read_document_truncates` limit)
- Modify: `tests/test_transcript_indexing.py` (append)

**Interfaces:**
- Consumes: `format_time` (Task 1); `chunks.start_s/end_s/kind` (Task 3).
- Produces: `SourceRef.start_s`; `SourceRef.cite()` → `[file @m:ss]`; `DocHit.start_s/end_s/kind`; `DocumentText.start_s` and `next_start_s`; `read_document(ctx, path, page=None, start_s=None)`.

- [ ] **Step 1: Write the failing tests**

Update `tests/test_mcp_server.py`: in `CASES`, change the `read_document` positional args to `("docs/kronika.txt", None, None)`.

Append to `tests/test_transcript_indexing.py`. Add to its imports:

```python
import json
from dataclasses import replace

from fixtures.transcripts import sample_rows

from genea.kb import KnowledgeBase
from genea.tool_runner import TRUNCATION_MARKER, run_tool
from genea.tools.base import ToolError, open_tools
from genea.tools.models import SourceRef
from genea.tools.search import read_document, search_documents
```

and append:

```python
@pytest.fixture
def ctx(kb):
    write_transcript(kb.root)
    index_kb(kb, FakeEmbedder())
    context = open_tools(kb, FakeEmbedder())
    yield context
    context.conn.close()


@pytest.mark.parametrize(("seconds", "cited"), [(0, "[a.txt @0:00]"), (840, "[a.txt @14:00]"),
                                                (3725, "[a.txt @1:02:05]")])
def test_cite_a_moment(seconds, cited):
    assert SourceRef(file="a.txt", start_s=seconds).cite() == cited  # 0 is a moment too, not "no time"


def test_search_hit_points_to_the_moment(ctx):
    hit = search_documents(ctx, "Prískupíc babke nasťahovali").hits[0]
    assert hit.file == REL and hit.kind == "text"
    assert hit.start_s <= 840 <= hit.end_s
    assert hit.source.cite() == f"[{REL} @{int(hit.start_s) // 60}:{int(hit.start_s) % 60:02d}]"


def test_read_transcript_from_the_start(ctx):
    doc = read_document(ctx, REL)
    assert doc.text.startswith("[0:00] Veta číslo 0")
    assert "[14:00] V roku 1954" in doc.text
    assert not doc.truncated and doc.next_start_s is None
    assert doc.sources[0].cite() == f"[{REL} @0:00]"


def test_read_transcript_from_a_moment(ctx):
    doc = read_document(ctx, REL, start_s=840)
    assert doc.text.startswith("[14:00] V roku 1954")
    assert doc.start_s == 840 and doc.sources[0].cite() == f"[{REL} @14:00]"


def test_read_transcript_window_continues_where_it_stopped(ctx):
    small = KnowledgeBase(ctx.kb.root, replace(ctx.kb.settings, tool_output_chars=1200))
    first = read_document(replace(ctx, kb=small), REL)
    # budget 1200 - 600 = 600; each line costs 108 characters of JSON, so five 15 s segments fit
    assert first.truncated and first.next_start_s == 75.0
    second = read_document(replace(ctx, kb=small), REL, start_s=first.next_start_s)
    assert second.text.startswith("[1:15]")


def test_read_transcript_refuses_wrong_arguments(ctx):
    with pytest.raises(ToolError, match="no pages"):
        read_document(ctx, REL, page=1)
    with pytest.raises(ToolError, match="between 0 and 899"):
        read_document(ctx, REL, start_s=900)
    with pytest.raises(ToolError, match="not a transcript"):
        read_document(ctx, "docs/kronika.txt", start_s=10)


def test_a_long_transcript_window_is_not_shrunk_by_the_tool_runner(kb):
    write_transcript(kb.root, "big.txt", sample_rows(300))
    index_kb(kb, FakeEmbedder())
    context = open_tools(kb, FakeEmbedder())
    try:
        out = run_tool(context, read_document, "docs/big.txt")
    finally:
        context.conn.close()
    body = json.loads(out.text)
    assert TRUNCATION_MARKER not in out.text and len(out.text) <= kb.settings.tool_output_chars
    assert len(body["text"]) > 9000
    assert body["truncated"] is True and body["next_start_s"] > 0


@pytest.mark.parametrize("content", ["Slovo o rodine. " * 2000,  # 32,000 characters of plain text
                                     'Povedal "áno" a odišiel.\n' * 1500])  # quotes and newlines cost double in JSON
def test_a_long_ordinary_document_is_not_shrunk_by_the_tool_runner(kb, content):
    (kb.root / "docs" / "long.txt").write_text(content, encoding="utf-8")
    index_kb(kb, FakeEmbedder())
    context = open_tools(kb, FakeEmbedder())
    try:
        out = run_tool(context, read_document, "docs/long.txt")
    finally:
        context.conn.close()
    body = json.loads(out.text)  # valid JSON with no runner marker: the runner did not have to shrink it
    assert TRUNCATION_MARKER not in out.text and len(out.text) <= kb.settings.tool_output_chars
    assert len(body["text"]) > 5000 and body["text"].endswith("…[truncated]") and body["truncated"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_transcript_indexing.py tests/test_mcp_server.py -v`
Expected: FAIL (`start_s` is not a field of `SourceRef`; `read_document() got an unexpected keyword 'start_s'`)

- [ ] **Step 3: Write the implementation**

`src/genea/tools/models.py`: add `from genea.ingest.transcript import format_time` to the imports, then

```python
class SourceRef(BaseModel):
    file: str
    line: int | None = None
    page: int | None = None
    chunk: int | None = None
    start_s: float | None = None

    def cite(self) -> str:
        if self.line is not None:
            return f"[{self.file}:L{self.line}]"
        if self.page is not None:
            return f"[{self.file} p.{self.page}]"
        if self.start_s is not None:
            return f"[{self.file} @{format_time(self.start_s)}]"
        return f"[{self.file}]"
```

Extend `DocHit` with `start_s: float | None = None`, `end_s: float | None = None`, `kind: str = "text"` (after `page`), and `DocumentText` with `start_s: float | None = None` and `next_start_s: float | None = None` (after `page`).

`src/genea/tools/search.py`: add `from genea.ingest.transcript import Segment, format_time`. In `search_documents` change the row query and hit construction:

```python
        row = ctx.conn.execute(
            "SELECT c.page, c.chunk_no, c.text, c.start_s, c.end_s, c.kind, f.path FROM chunks c "
            "JOIN files f ON f.id = c.file_id WHERE c.id = ?", (chunk_id,)).fetchone()
        source = SourceRef(file=row["path"], page=row["page"], chunk=row["chunk_no"], start_s=row["start_s"])
        hits.append(DocHit(file=row["path"], page=row["page"], chunk=row["chunk_no"], start_s=row["start_s"],
                           end_s=row["end_s"], kind=row["kind"], text=row["text"][:1500],
                           score=round(score, 4), source=source))
```

Replace `read_document` and add the transcript reader above it:

```python
RESULT_OVERHEAD = 600  # characters of tool_output_chars kept for the other fields of a DocumentText
TRUNCATED = "\n…[truncated]"


def _json_len(text: str) -> int:
    """Characters `text` takes inside the tool's JSON output (quotes, backslashes and newlines are escaped)."""
    return len(json.dumps(text, ensure_ascii=False)) - 2


def _text_budget(ctx: ToolContext) -> int:
    """Document text larger than this would make the result exceed tool_output_chars, and the tool runner
    would then cut every string to 2,000 characters."""
    return max(ctx.kb.settings.tool_output_chars - RESULT_OVERHEAD, 200)


def _fit(text: str, budget: int) -> tuple[str, bool]:
    """The longest prefix of `text` (plus the truncation marker) whose escaped form fits `budget`."""
    if _json_len(text) <= budget:
        return text, False
    room = budget - _json_len(TRUNCATED)
    cut = text[:room]
    while cut and _json_len(cut) > room:
        cut = cut[:len(cut) - max(1, _json_len(cut) - room)]
    return cut + TRUNCATED, True


def _read_transcript(ctx: ToolContext, rel: str, segments: list[Segment], page: int | None,
                     start_s: float | None) -> DocumentText:
    if page is not None:
        raise ToolError(f"{rel} is a transcript and has no pages; use start_s (seconds) to read from a moment")
    start = 0.0 if start_s is None else start_s
    duration = max(s.end for s in segments)
    if start < 0 or start >= duration:
        raise ToolError(f"start_s must be between 0 and {duration:.0f} (the length of the recording in seconds)")
    budget = _text_budget(ctx)
    lines: list[str] = []
    used, first, next_start = 0, None, None
    for seg in segments:
        if seg.end <= start:
            continue
        line = f"[{format_time(seg.start)}] {seg.text}"
        cost = _json_len(line) + 2  # the line and its escaped newline
        if lines and used + cost > budget:
            next_start = seg.start
            break
        first = seg.start if first is None else first
        lines.append(line)
        used += cost
    return DocumentText(file=rel, start_s=first, next_start_s=next_start, text="\n".join(lines),
                        truncated=next_start is not None, sources=[SourceRef(file=rel, start_s=first)])


def read_document(ctx: ToolContext, path: str, page: int | None = None,
                  start_s: float | None = None) -> DocumentText:
    try:
        full_path = ctx.kb.resolve_inside(path)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if not full_path.is_file() or full_path.suffix.lower() not in DOC_EXTS:
        raise ToolError(f"Not a readable document: {path} (use search_documents to find documents)")
    extract = extract_document(full_path)
    rel = ctx.kb.rel(full_path)
    if extract.segments:
        return _read_transcript(ctx, rel, extract.segments, page, start_s)
    if start_s is not None:
        raise ToolError(f"{rel} is not a transcript, so start_s does not apply (use page for PDFs)")
    pages = extract.pages if page is None else [p for p in extract.pages if p.number == page]
    if page is not None and not pages:
        raise ToolError(f"Page {page} was not found or has no text in {path}")
    text = "\n\n".join((f"[page {p.number}]\n" if p.number else "") + p.text for p in pages)
    text, truncated = _fit(text, _text_budget(ctx))  # before: text[:limit] overflowed and was cut to 2,000
    return DocumentText(file=rel, page=page, text=text, truncated=truncated,
                        sources=[SourceRef(file=rel, page=page)])
```

`search.py` already imports `json`.

`src/genea/agent/core.py` and `src/genea/mcp_server.py`: replace the `read_document` wrappers. The two docstrings must be identical (a test compares them).

```python
    @agent.tool
    async def read_document(ctx: RunContext[ToolContext], path: str, page: int | None = None,
                            start_s: float | None = None) -> str:
        """Read the full text of a document (or one PDF page) found by search_documents. For a recording
        transcript give start_s (seconds) to read from that moment; if next_start_s is returned, call again with it."""
        return _call(ctx, search.read_document, path, page, start_s)
```

```python
    @server.tool(structured_output=False)
    async def read_document(path: str, page: int | None = None, start_s: float | None = None) -> str:
        """Read the full text of a document (or one PDF page) found by search_documents. For a recording
        transcript give start_s (seconds) to read from that moment; if next_start_s is returned, call again with it."""
        return run(search.read_document, path, page, start_s)
```

Also change the `search_documents` docstring in both files to end with
`Returns passages with file and page (or, for recordings, the time in the recording).`

- [ ] **Step 4: Run the whole suite to verify it passes**

Run: `uv run pytest -q`
Expected: PASS, after one change to an existing test: in `tests/test_search.py::test_read_document_truncates` replace `tool_output_chars=20` with `tool_output_chars=650`. With 20 the text budget is its floor of 200 characters, larger than the short chronicle, so nothing would be truncated; 650 gives a budget of 50.

- [ ] **Step 5: Commit**

```bash
git add src/genea tests
git commit -m "$(cat <<'EOF'
Cite transcripts by time and read them from a moment

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Summarizer

**Files:**
- Create: `src/genea/ingest/summarize.py`
- Modify: `src/genea/config.py` (`summary_model`)
- Create: `tests/test_summarize.py`

**Interfaces:**
- Consumes: `Segment`, `format_time` (Task 1).
- Produces: `SectionSummary(start_s, end_s, summary, topics, people, places, years)`; `Summary(overall, sections)` with `to_dict()` / `from_dict(data)`; `split_sections(segments, max_chars=12000) -> list[list[Segment]]`; `Summarizer` protocol (`model: str`, `summarize(segments) -> Summary`); `FakeSummarizer(section_chars=12000)` with a `calls` counter; `OllamaSummarizer(url, model, timeout=600.0, client=None, section_chars=12000)`; `make_summarizer(settings) -> OllamaSummarizer | None`; `SummaryError`, `SummaryUnavailable(SummaryError)`; `Settings.summary_model: str = ""`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_summarize.py`:

```python
import json

import httpx
import pytest

from genea.config import Settings
from genea.ingest.summarize import (
    FakeSummarizer,
    OllamaSummarizer,
    SectionSummary,
    Summary,
    SummaryError,
    SummaryUnavailable,
    make_summarizer,
    split_sections,
)
from genea.ingest.transcript import Segment

SEGMENTS = [Segment(0, 10, "a" * 60), Segment(10, 20, "b" * 60), Segment(20, 30, "c" * 60)]


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _reply(payload):
    return httpx.Response(200, json={"message": {"content": json.dumps(payload, ensure_ascii=False)}})


def test_split_sections_at_segment_boundaries():
    sections = split_sections(SEGMENTS, max_chars=130)
    assert [[s.start for s in part] for part in sections] == [[0, 10], [20]]
    assert split_sections([], 130) == []


def test_a_segment_longer_than_the_limit_gets_its_own_section():
    big = [Segment(0, 5, "x" * 500), Segment(5, 6, "y")]
    assert [len(part) for part in split_sections(big, max_chars=100)] == [1, 1]


def test_summary_round_trip():
    summary = Summary("Celkovo.", [SectionSummary(0, 10, "Časť.", ["rodina"], ["babka Fábiková"], ["Prískupice"], ["1954"])])
    assert Summary.from_dict(json.loads(json.dumps(summary.to_dict()))) == summary


def test_fake_summarizer_is_deterministic_and_counts_calls():
    fake = FakeSummarizer(section_chars=130)
    first, second = fake.summarize(SEGMENTS), fake.summarize(SEGMENTS)
    assert first == second and fake.calls == 2
    assert [(s.start_s, s.end_s) for s in first.sections] == [(0, 20), (20, 30)]


def test_ollama_summarizer_one_section():
    seen = []

    def handler(request):
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        seen.append(body)
        return _reply({"summary": "Rozprávanie o babke.", "topics": ["rodina"], "people": ["babka Fábiková"],
                       "places": ["Prískupice"], "years": [1954, "1953"]})

    summarizer = OllamaSummarizer("http://ollama:11434", "qwen3:14b", client=_client(handler))
    summary = summarizer.summarize(SEGMENTS)
    assert len(seen) == 1  # one section needs no second pass
    assert seen[0]["model"] == "qwen3:14b" and seen[0]["think"] is False and seen[0]["format"] == "json"
    assert seen[0]["options"]["num_ctx"] >= 8192  # Ollama's default window can be smaller than one section
    assert seen[0]["messages"][1]["content"] == " ".join(s.text for s in SEGMENTS)
    assert summary.overall == "Rozprávanie o babke."
    assert summary.sections == [SectionSummary(0, 30, "Rozprávanie o babke.", ["rodina"], ["babka Fábiková"],
                                               ["Prískupice"], ["1954", "1953"])]


def test_ollama_summarizer_several_sections_then_overall():
    answers = iter([{"summary": "Prvá časť."}, {"summary": "Druhá časť."}, {"overall": "Celá nahrávka."}])
    prompts = []

    def handler(request):
        prompts.append(json.loads(request.content)["messages"][1]["content"])
        return _reply(next(answers))

    summarizer = OllamaSummarizer("http://ollama:11434", "m", client=_client(handler), section_chars=130)
    summary = summarizer.summarize(SEGMENTS)
    assert [s.summary for s in summary.sections] == ["Prvá časť.", "Druhá časť."]
    assert summary.overall == "Celá nahrávka."
    assert prompts[2] == "[0:00] Prvá časť.\n[0:20] Druhá časť."


@pytest.mark.parametrize("content", ["not json at all", "[1, 2]", '{"topics": ["x"]}', ""])
def test_malformed_model_output_is_a_summary_error(content):
    def handler(request):
        return httpx.Response(200, json={"message": {"content": content}})

    with pytest.raises(SummaryError):
        OllamaSummarizer("http://ollama:11434", "m", client=_client(handler)).summarize(SEGMENTS)


def test_missing_message_is_a_summary_error():
    with pytest.raises(SummaryError):
        OllamaSummarizer("http://ollama:11434", "m", client=_client(lambda r: httpx.Response(200, json={}))).summarize(SEGMENTS)


def test_http_error_is_a_summary_error_naming_the_model():
    summarizer = OllamaSummarizer("http://ollama:11434", "qwen3:14b",
                                  client=_client(lambda r: httpx.Response(404, json={"error": "model not found"})))
    with pytest.raises(SummaryError, match="qwen3:14b") as excinfo:
        summarizer.summarize(SEGMENTS)
    assert not isinstance(excinfo.value, SummaryUnavailable)


def test_unreachable_ollama_is_unavailable():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(SummaryUnavailable, match="not reachable"):
        OllamaSummarizer("http://ollama:11434", "m", client=_client(handler)).summarize(SEGMENTS)


def test_empty_transcript_is_a_summary_error():
    with pytest.raises(SummaryError, match="no text"):
        OllamaSummarizer("http://ollama:11434", "m", client=_client(lambda r: _reply({}))).summarize([])


def test_make_summarizer_model_choice():
    assert make_summarizer(Settings()).model == "qwen3:14b"  # the chat model's name
    assert make_summarizer(Settings(model="ollama:qwen3:8b", summary_model="gemma3:12b")).model == "gemma3:12b"
    assert make_summarizer(Settings(model="anthropic:claude-sonnet-5-5")) is None  # no local model to use
    assert make_summarizer(Settings(model="anthropic:claude-sonnet-5-5", summary_model="qwen3:14b")).model == "qwen3:14b"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_summarize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genea.ingest.summarize'`

- [ ] **Step 3: Write the implementation**

In `src/genea/config.py` add the field after `exiftool`:

```python
    summary_model: str = ""  # Ollama model for transcript summaries; empty: the chat model, if it is an ollama: one
```

Create `src/genea/ingest/summarize.py`:

```python
"""Summaries of transcripts: sections of ~12,000 characters, then one overall summary."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Protocol

import httpx

from genea.config import Settings
from genea.ingest.transcript import Segment, format_time

SECTION_CHARS = 12000
NUM_CTX = 8192  # tokens: a 12,000-character Slovak section is ~4-5k tokens, and Ollama's default window may be smaller
REQUEST_TIMEOUT = 600.0  # seconds: a local model can need minutes for one section

SECTION_PROMPT = """\
You summarize one part of a recorded family interview for a genealogy archive.
The text is a raw speech-to-text transcript (Slovak or Czech, no punctuation structure, names may be misspelled).
Write in the language of the transcript. Use ONLY what is said; add no facts. Mark a name you are unsure of with "?".
Reply with JSON only: {"summary": "3-5 sentences", "topics": [...], "people": [...], "places": [...], "years": [...]}
people are names or kin terms as spoken (for example "babka Fábiková"); years are four-digit years that are mentioned."""

OVERALL_PROMPT = """\
You are given summaries of consecutive parts of one recorded family interview.
Write an overall summary of 3-6 sentences in the same language as the parts. Use only what the parts say.
Reply with JSON only: {"overall": "..."}"""


class SummaryError(RuntimeError):
    """A summary could not be produced."""


class SummaryUnavailable(SummaryError):
    """Ollama cannot be reached: the next transcript would fail the same way."""


@dataclass
class SectionSummary:
    start_s: float
    end_s: float
    summary: str
    topics: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    years: list[str] = field(default_factory=list)


@dataclass
class Summary:
    overall: str
    sections: list[SectionSummary]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Summary:
        return cls(str(data["overall"]), [SectionSummary(**s) for s in data["sections"]])


class Summarizer(Protocol):
    model: str

    def summarize(self, segments: list[Segment]) -> Summary: ...


def split_sections(segments: list[Segment], max_chars: int = SECTION_CHARS) -> list[list[Segment]]:
    """Consecutive runs of segments of up to `max_chars` characters; a longer segment is alone."""
    sections: list[list[Segment]] = []
    current: list[Segment] = []
    length = 0
    for seg in segments:
        if current and length + len(seg.text) + 1 > max_chars:
            sections.append(current)
            current, length = [], 0
        current.append(seg)
        length += len(seg.text) + 1
    if current:
        sections.append(current)
    return sections


class FakeSummarizer:
    """Deterministic and offline, for tests."""

    model = "fake"

    def __init__(self, section_chars: int = SECTION_CHARS) -> None:
        self.section_chars = section_chars
        self.calls = 0

    def summarize(self, segments: list[Segment]) -> Summary:
        self.calls += 1
        sections = [SectionSummary(part[0].start, part[-1].end, "Súhrn: " + " ".join(s.text for s in part)[:80],
                                   topics=["rodina"])
                    for part in split_sections(segments, self.section_chars)]
        return Summary(f"Nahrávka o rodine, {len(sections)} častí.", sections)


def _text(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SummaryError(f"the model's answer has no '{key}'")
    return value.strip()


def _strings(data: dict, key: str) -> list[str]:
    value = data.get(key)
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


class OllamaSummarizer:
    def __init__(self, url: str, model: str, timeout: float = REQUEST_TIMEOUT,
                 client: httpx.Client | None = None, section_chars: int = SECTION_CHARS) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.client = client or httpx.Client()
        self.section_chars = section_chars

    def summarize(self, segments: list[Segment]) -> Summary:
        parts = split_sections(segments, self.section_chars)
        if not parts:
            raise SummaryError("the transcript has no text")
        sections = []
        for part in parts:
            data = self._ask(SECTION_PROMPT, " ".join(s.text for s in part))
            sections.append(SectionSummary(part[0].start, part[-1].end, _text(data, "summary"),
                                           _strings(data, "topics"), _strings(data, "people"),
                                           _strings(data, "places"), _strings(data, "years")))
        if len(sections) == 1:
            return Summary(sections[0].summary, sections)
        joined = "\n".join(f"[{format_time(s.start_s)}] {s.summary}" for s in sections)
        return Summary(_text(self._ask(OVERALL_PROMPT, joined), "overall"), sections)

    def _ask(self, system: str, content: str) -> dict:
        payload = {"model": self.model, "stream": False, "think": False, "format": "json",
                   "options": {"temperature": 0, "num_ctx": NUM_CTX},
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]}
        try:
            response = self.client.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout)
            response.raise_for_status()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise SummaryUnavailable(f"Ollama is not reachable ({exc}). Is it running?") from exc
        except httpx.HTTPError as exc:
            raise SummaryError(f"Ollama summary request failed ({exc}). Is `{self.model}` pulled?") from exc
        try:
            data = json.loads(response.json()["message"]["content"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SummaryError("Ollama did not return the JSON that was asked for") from exc
        if not isinstance(data, dict):
            raise SummaryError("Ollama did not return a JSON object")
        return data


def make_summarizer(settings: Settings) -> OllamaSummarizer | None:
    """None when there is no local model to use: set summary_model, or use an ollama: chat model."""
    name = settings.summary_model or (
        settings.model.removeprefix("ollama:") if settings.model.startswith("ollama:") else "")
    return OllamaSummarizer(settings.ollama_url, name) if name else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_summarize.py tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/genea/ingest/summarize.py src/genea/config.py tests/test_summarize.py
git commit -m "$(cat <<'EOF'
Add a transcript summarizer with a fake for tests

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Summary cache

**Files:**
- Modify: `src/genea/kb.py` (`summaries_dir`, `SUBDIRS`)
- Modify: `tests/test_kb.py` (`SUBDIRS` list)
- Create: `src/genea/index/summary_cache.py`
- Create: `tests/test_summary_cache.py`

**Interfaces:**
- Consumes: `Summary` (Task 5).
- Produces: `KnowledgeBase.summaries_dir`; `SUMMARY_VERSION = 1`; `cache_path(kb, digest) -> Path`; `load(kb, digest) -> Summary | None` (None for a missing, corrupt, malformed or old-version file); `store(kb, digest, summary, model) -> None` (atomic).

- [ ] **Step 1: Write the failing tests**

In `tests/test_kb.py` add `"summaries"` to the `SUBDIRS` list. Create `tests/test_summary_cache.py`:

```python
import json

from genea.index import summary_cache
from genea.ingest.summarize import SectionSummary, Summary

SUMMARY = Summary("Celkovo.", [SectionSummary(0, 30, "Časť.", ["rodina"])])


def test_store_then_load(kb):
    summary_cache.store(kb, "abc123", SUMMARY, "qwen3:14b")
    assert summary_cache.load(kb, "abc123") == SUMMARY
    data = json.loads((kb.summaries_dir / "abc123.json").read_text(encoding="utf-8"))
    assert data["version"] == summary_cache.SUMMARY_VERSION and data["model"] == "qwen3:14b"
    assert data["overall"] == "Celkovo." and data["sections"][0]["summary"] == "Časť."
    assert list(kb.summaries_dir.glob("*.tmp")) == []  # written atomically


def test_missing_entry_is_a_miss(kb):
    assert summary_cache.load(kb, "nope") is None


def test_an_older_version_is_a_miss(kb, monkeypatch):
    summary_cache.store(kb, "abc123", SUMMARY, "m")
    monkeypatch.setattr(summary_cache, "SUMMARY_VERSION", summary_cache.SUMMARY_VERSION + 1)
    assert summary_cache.load(kb, "abc123") is None


def test_a_different_model_is_still_a_hit(kb):
    summary_cache.store(kb, "abc123", SUMMARY, "old-model")
    assert summary_cache.load(kb, "abc123") == SUMMARY  # the model is recorded, not part of the key


def test_damaged_files_are_a_miss(kb):
    for name, content in [("a", "{not json"), ("b", "[]"), ("c", '{"version": 1}'),
                          ("d", json.dumps({"version": 1, "overall": "x", "sections": [{"oops": 1}]}))]:
        (kb.summaries_dir / f"{name}.json").write_text(content, encoding="utf-8")
        assert summary_cache.load(kb, name) is None
    (kb.summaries_dir / "e.json").write_bytes(b"\xff\xfe\x00")
    assert summary_cache.load(kb, "e") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_summary_cache.py tests/test_kb.py -v`
Expected: FAIL (`No module named 'genea.index.summary_cache'`; `summaries` directory missing)

- [ ] **Step 3: Write the implementation**

In `src/genea/kb.py` add `"summaries"` to `SUBDIRS` and the property:

```python
    @property
    def summaries_dir(self) -> Path:
        return self.meta_dir / "summaries"
```

Create `src/genea/index/summary_cache.py`:

```python
"""Summaries cached beside the index, keyed by the transcript's sha256.

They live outside index.db on purpose: `genea index --rebuild` deletes the database, and rebuilding must
not run the LLM again for transcripts that have not changed. Changing `summary_model` does not invalidate
an entry (the model is only recorded); bump SUMMARY_VERSION when the prompt or format improves.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from genea.ingest.summarize import Summary
from genea.kb import KnowledgeBase

SUMMARY_VERSION = 1


def cache_path(kb: KnowledgeBase, digest: str) -> Path:
    return kb.summaries_dir / f"{digest}.json"


def load(kb: KnowledgeBase, digest: str) -> Summary | None:
    """None for a missing, damaged or out-of-date entry: the caller then summarizes again."""
    try:
        data = json.loads(cache_path(kb, digest).read_text(encoding="utf-8"))
        if data["version"] != SUMMARY_VERSION:
            return None
        return Summary.from_dict(data)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def store(kb: KnowledgeBase, digest: str, summary: Summary, model: str) -> None:
    path = cache_path(kb, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"version": SUMMARY_VERSION, "model": model, **summary.to_dict()},
                              ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_summary_cache.py tests/test_kb.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/genea/kb.py src/genea/index/summary_cache.py tests/test_kb.py tests/test_summary_cache.py
git commit -m "$(cat <<'EOF'
Cache transcript summaries outside the index

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Summaries in the index

**Files:**
- Modify: `src/genea/ingest/docs.py` (`summary_chunks`)
- Modify: `src/genea/index/indexer.py` (`_ingest_doc`, `_index_one`, `_index_locked`, `index_kb`, `_summarize_pending`, `rebuild_links`)
- Modify: `src/genea/tools/conflicts.py` (`_document_mismatches`: cite the moment of a transcript passage)
- Modify: `tests/test_transcript_indexing.py` (append)

**Interfaces:**
- Consumes: `FakeSummarizer`, `Summary`, `SummaryError`, `SummaryUnavailable` (Task 5); `summary_cache.load/store` (Task 6); `read_transcript` (Task 2).
- Produces: `summary_chunks(summary, first_no, duration) -> list[Chunk]`; `index_kb(kb, embedder, rebuild=False, on_progress=None, summarizer=None)`; constants `SUMMARY_PENDING = "summary pending"` in `indexer`; a transcript without a cached summary is stored with `files.error_msg = "summary pending: <reason>"`; `progress.phase("summarizing", total=n)` with `current` = the transcript path; `rebuild_links` reads only `kind = 'text'` chunks; transcript conflicts cite `[file @m:ss]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_transcript_indexing.py`. Add to its imports:

```python
from genea.index import summary_cache
from genea.index.indexer import SUMMARY_PENDING
from genea.ingest.summarize import FakeSummarizer, SummaryError, SummaryUnavailable
```

and append:

```python
def _digest(path):
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_row(kb, rel=REL):
    return _query(kb, "SELECT status, error_msg FROM files WHERE path = ?", (rel,))[0]


class Failing(FakeSummarizer):
    def summarize(self, segments):
        self.calls += 1
        raise SummaryError("model exploded")


class Down(FakeSummarizer):
    def summarize(self, segments):
        self.calls += 1
        raise SummaryUnavailable("Ollama is not reachable (refused). Is it running?")


def test_summary_chunks_are_indexed_and_cached(kb):
    path = write_transcript(kb.root)
    fake = FakeSummarizer(section_chars=3000)
    index_kb(kb, FakeEmbedder(), summarizer=fake)
    summaries = _chunks(kb, kind="summary")
    assert len(summaries) == 1 + 3  # the overall summary and one per section (~6,000 characters / 3,000)
    overall = summaries[0]
    assert (overall["start_s"], overall["end_s"], overall["page"]) == (0.0, 899.0, None)  # to the last segment's end
    assert overall["text"].startswith("Zhrnutie (generované): ")
    assert all(s["end_s"] > s["start_s"] and "rodina" in s["text"] for s in summaries[1:])
    assert [c["chunk_no"] for c in _chunks(kb) + summaries] == list(range(len(_chunks(kb)) + len(summaries)))
    assert summary_cache.load(kb, _digest(path)) is not None
    assert _file_row(kb)["error_msg"] is None
    chunks = _query(kb, "SELECT COUNT(*) FROM chunks")[0][0]
    assert _query(kb, "SELECT COUNT(*) FROM chunks_fts")[0][0] == chunks
    assert _query(kb, "SELECT COUNT(*) FROM chunks_vec")[0][0] == chunks


def test_summary_hit_points_to_the_transcript_time(kb):
    write_transcript(kb.root)
    index_kb(kb, FakeEmbedder(), summarizer=FakeSummarizer(section_chars=3000))
    context = open_tools(kb, FakeEmbedder())
    try:
        hits = [h for h in search_documents(context, "Súhrn rodine", k=20).hits if h.kind == "summary"]
    finally:
        context.conn.close()
    assert hits and all(h.file == REL for h in hits)
    assert all(h.source.cite().startswith(f"[{REL} @") for h in hits)


def test_an_unchanged_transcript_is_not_summarized_again(kb):
    write_transcript(kb.root)
    fake = FakeSummarizer()
    index_kb(kb, FakeEmbedder(), summarizer=fake)
    report = index_kb(kb, FakeEmbedder(), summarizer=fake)
    assert fake.calls == 1 and report.ok == 0


def test_rebuild_reuses_the_cache(kb):
    write_transcript(kb.root)
    fake = FakeSummarizer()
    index_kb(kb, FakeEmbedder(), summarizer=fake)
    index_kb(kb, FakeEmbedder(), rebuild=True, summarizer=fake)
    assert fake.calls == 1
    assert _chunks(kb, kind="summary")


def test_a_changed_transcript_is_summarized_again(kb):
    path = write_transcript(kb.root)
    fake = FakeSummarizer()
    index_kb(kb, FakeEmbedder(), summarizer=fake)
    old = _digest(path)
    write_transcript(kb.root, rows=sample_rows()[:40])
    index_kb(kb, FakeEmbedder(), summarizer=fake)
    assert fake.calls == 2 and _digest(path) != old
    assert "Nahrávka o rodine" in _chunks(kb, kind="summary")[0]["text"]
    assert _query(kb, "SELECT COUNT(*) FROM chunks_fts")[0][0] == _query(kb, "SELECT COUNT(*) FROM chunks")[0][0]


def test_a_failing_summarizer_keeps_the_transcript_searchable_and_is_retried(kb):
    write_transcript(kb.root)
    report = index_kb(kb, FakeEmbedder(), summarizer=Failing())
    row = _file_row(kb)
    assert row["status"] == "ok" and row["error_msg"] == f"{SUMMARY_PENDING}: SummaryError: model exploded"
    assert (REL, row["error_msg"]) in report.problems
    assert _chunks(kb) and not _chunks(kb, kind="summary")
    fake = FakeSummarizer()
    report = index_kb(kb, FakeEmbedder(), summarizer=fake)  # unchanged file, but pending: tried again
    assert fake.calls == 1 and report.ok == 1
    assert _file_row(kb)["error_msg"] is None and _chunks(kb, kind="summary")


def test_without_a_summarizer_the_transcript_is_marked_and_filled_in_later(kb):
    write_transcript(kb.root)
    index_kb(kb, FakeEmbedder())
    assert _file_row(kb)["error_msg"] == f"{SUMMARY_PENDING}: summaries disabled"
    assert _chunks(kb) and not _chunks(kb, kind="summary")
    index_kb(kb, FakeEmbedder())  # still no summarizer: nothing to retry, the file is skipped
    fake = FakeSummarizer()
    index_kb(kb, FakeEmbedder(), summarizer=fake)
    assert fake.calls == 1 and _file_row(kb)["error_msg"] is None


def test_ollama_down_stops_after_the_first_transcript(kb):
    for number in (1, 2, 3):
        write_transcript(kb.root, f"2023_01_0{number}-rozhovor.txt")
    down = Down()
    report = index_kb(kb, FakeEmbedder(), summarizer=down)
    assert down.calls == 1  # not one timeout per recording
    assert report.errors == 0
    for number in (1, 2, 3):
        row = _file_row(kb, f"docs/2023_01_0{number}-rozhovor.txt")
        assert row["status"] == "ok" and "not reachable" in row["error_msg"]
        assert _chunks(kb, f"docs/2023_01_0{number}-rozhovor.txt")


def test_ordinary_text_is_never_summarized(kb):
    fake = FakeSummarizer()
    index_kb(kb, FakeEmbedder(), summarizer=fake)
    assert fake.calls == 0
    assert not _query(kb, "SELECT 1 FROM files WHERE error_msg LIKE 'summary pending%'")


JOSEF_ROW = (0.0, 5.0, "Josef Novák odišiel v roku 1700 do sveta.")  # a year that matches none of his events


def _links_by_kind(kb):
    return {r["kind"]: r["n"] for r in _query(
        kb, "SELECT c.kind, COUNT(*) AS n FROM links l JOIN chunks c ON l.source_kind = 'chunk' "
            "AND c.id = l.source_id JOIN files f ON f.id = c.file_id WHERE f.path = ? GROUP BY c.kind", (REL,))}


def test_summary_chunks_link_no_people(kb):
    write_transcript(kb.root, rows=[JOSEF_ROW, *sample_rows()[1:]])
    index_kb(kb, FakeEmbedder(), summarizer=FakeSummarizer())  # the fake summary repeats "Josef Novák"
    assert "Josef Novák" in _chunks(kb, kind="summary")[1]["text"]
    kinds = _links_by_kind(kb)
    assert kinds.get("text") and "summary" not in kinds  # only the transcript itself links people


def test_a_transcript_mismatch_cites_the_moment(kb):
    from genea.tools.conflicts import find_conflicts

    write_transcript(kb.root, rows=[JOSEF_ROW, *sample_rows()[1:]])
    index_kb(kb, FakeEmbedder(), summarizer=FakeSummarizer())
    context = open_tools(kb, FakeEmbedder())
    try:
        found = [c for c in find_conflicts(context).conflicts
                 if c.kind == "document_mismatch" and c.sources[0].file == REL]
    finally:
        context.conn.close()
    assert found  # from the transcript text, not from the summary
    assert all(c.sources[0].cite() == f"[{REL} @0:00]" for c in found)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_transcript_indexing.py -v`
Expected: FAIL (`index_kb() got an unexpected keyword argument 'summarizer'`; `cannot import name 'SUMMARY_PENDING'`)

- [ ] **Step 3: Add `summary_chunks` to `src/genea/ingest/docs.py`**

Add `from genea.ingest.summarize import Summary` to the imports and append:

```python
SUMMARY_LABEL = "Zhrnutie (generované)"


def summary_chunks(summary: Summary, first_no: int, duration: float) -> list[Chunk]:
    """A generated summary as chunks of kind 'summary': the overall one spans the whole recording, the
    others a section each. They say where to look; they are never evidence."""
    chunks = [Chunk(None, first_no, f"{SUMMARY_LABEL}: {summary.overall}", 0.0, duration, "summary")]
    for section in summary.sections:
        extras = [f"{label}: {', '.join(values)}"
                  for label, values in (("Témy", section.topics), ("Osoby", section.people),
                                        ("Miesta", section.places), ("Roky", section.years)) if values]
        text = " ".join([f"{SUMMARY_LABEL}: {section.summary}", *extras])
        chunks.append(Chunk(None, first_no + len(chunks), text, section.start_s, section.end_s, "summary"))
    return chunks
```

- [ ] **Step 4: Change `src/genea/index/indexer.py`**

Imports: add `from genea.index import summary_cache`, `from genea.ingest.summarize import Summarizer, SummaryUnavailable`, and extend the docs import to
`from genea.ingest.docs import DOC_EXTS, chunk_pages, chunk_segments, extract_document, read_transcript, summary_chunks`.

Constants (below `PROGRESS_INTERVAL`):

```python
SUMMARY_PENDING = "summary pending"  # prefix of files.error_msg for a transcript that still lacks its summary
NOT_GENERATED = "not generated"
SUMMARIES_OFF = "summaries disabled"
```

Replace `_ingest_doc` with:

```python
def _ingest_doc(conn, kb, embedder, path, file_id, digest="", pending_reason=NOT_GENERATED) -> tuple[str, str | None]:
    extract = extract_document(path)
    if extract.needs_ocr:
        return "needs_ocr", "no text layer; OCR is needed"
    message = None
    if extract.segments:  # (chunk, text for FTS and the embedder): the recording's name adds searchable context
        header = transcript_header(path.stem)
        chunks = chunk_segments(extract.segments)
        items = [(c, f"{header}\n{c.text}") for c in chunks]
        summary = summary_cache.load(kb, digest)
        if summary is None:
            message = f"{SUMMARY_PENDING}: {pending_reason}"
        else:
            items += [(c, f"{header}\n{c.text}")
                      for c in summary_chunks(summary, len(chunks), extract.segments[-1].end)]
    else:
        items = [(c, c.text) for c in chunk_pages(extract.pages)]
    vectors: list[list[float]] = []
    for i in range(0, len(items), EMBED_BATCH):
        vectors += embedder.embed([indexed for _, indexed in items[i:i + EMBED_BATCH]])
    for (chunk, indexed), vector in zip(items, vectors):
        chunk_id = conn.execute(
            "INSERT INTO chunks (file_id, page, chunk_no, text, start_s, end_s, kind) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (file_id, chunk.page, chunk.chunk_no, chunk.text, chunk.start_s, chunk.end_s, chunk.kind),
        ).lastrowid
        conn.execute("INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (chunk_id, indexed))
        conn.execute("INSERT INTO chunks_vec (chunk_id, embedding) VALUES (?, ?)",
                     (chunk_id, sqlite_vec.serialize_float32(vector)))
    return "ok", message
```

In `_index_one`, extend the signature and the `extra` line:

```python
def _index_one(conn, kb, embedder, path, kind, digest, old_id, report: IndexReport,
               record: ImageRecord | None = None, pending_reason: str = NOT_GENERATED) -> None:
    """`record` is the image's metadata when it was already read (in a batch)."""
```

```python
            if kind == "image":
                extra = {"record": record}
            elif kind == "doc":
                extra = {"digest": digest, "pending_reason": pending_reason}
            else:
                extra = {}
```

Add these two helpers above `index_kb`:

```python
def _summary_pending(row) -> bool:
    return row is not None and (row["error_msg"] or "").startswith(SUMMARY_PENDING)


def _summarize_pending(kb: KnowledgeBase, summarizer: Summarizer, pending: list,
                       progress: _Progress) -> dict[str, str]:
    """Summarize the pending transcripts that have no cached summary, before any database transaction
    opens. Returns {path: reason} for the ones that failed; they are indexed without a summary and
    retried on the next run. When Ollama cannot be reached the remaining ones are not tried at all."""
    todo = []
    for path, kind, rel, digest, _old in pending:
        if kind != "doc" or path.suffix.lower() != ".txt" or summary_cache.load(kb, digest) is not None:
            continue
        try:
            segments = read_transcript(path)
        except Exception:  # noqa: BLE001 - the indexing step records an unreadable file
            continue
        if segments:
            todo.append((rel, digest, segments))
    failures: dict[str, str] = {}
    if not todo:
        return failures
    progress.phase("summarizing", total=len(todo))
    for done, (rel, digest, segments) in enumerate(todo):
        progress.update(done, rel)
        try:
            summary_cache.store(kb, digest, summarizer.summarize(segments), summarizer.model)
        except SummaryUnavailable as exc:
            failures.update({r: str(exc) for r, _, _ in todo[done:]})
            break
        except Exception as exc:  # noqa: BLE001 - one bad summary must not stop indexing
            failures[rel] = f"{type(exc).__name__}: {exc}"
    return failures
```

Replace `index_kb` and `_index_locked` (the parts that change are the `summarizer` parameter, the `error_msg` column, the `retry` condition, the pre-pass and the `pending_reason` argument):

```python
def index_kb(kb: KnowledgeBase, embedder: Embedder, rebuild: bool = False,
             on_progress: Callable[[IndexProgress], None] | None = None,
             summarizer: Summarizer | None = None) -> IndexReport:
    """Raises IndexBusy when another run holds the lock, IndexInUse when a rebuild cannot delete the index,
    and IndexDbLocked when a program that ignores the lock keeps the database locked. Without a
    `summarizer` transcripts are indexed without summaries and marked, so a later run adds them."""
    with IndexLock(kb, wait=LOCK_WAIT):
        try:
            return _index_locked(kb, embedder, rebuild, _Progress(kb, on_progress), summarizer)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc):
                raise IndexDbLocked() from exc
            raise
        finally:
            clear_progress(kb)


def _index_locked(kb: KnowledgeBase, embedder: Embedder, rebuild: bool, progress: _Progress,
                  summarizer: Summarizer | None) -> IndexReport:
    if rebuild and kb.index_path.exists():
        try:
            kb.index_path.unlink()
        except PermissionError as exc:  # Windows: still open, typically by `genea mcp`
            raise IndexInUse() from exc
    report = IndexReport()
    conn = open_index(kb, embedder)
    try:
        set_index_state(conn, "running")  # stays 'running' if this run dies: seen as interrupted
        known = {r["path"]: r for r in conn.execute(
            "SELECT id, path, sha256, mtime, status, error_msg FROM files")}
        seen: set[str] = set()
        pending = []  # (path, kind, rel, digest, old row) of the files that need indexing
        for path, kind in scan_files(kb):
            rel = kb.rel(path)
            seen.add(rel)
            progress.update(len(seen))
            old = known.get(rel)
            retry = summarizer is not None and _summary_pending(old)  # a transcript still waiting for its summary
            try:
                mtime = source_mtime(path, kind)
                if old is not None and old["status"] != "error" and old["mtime"] == mtime and not retry:
                    report.skipped += 1  # same modification time: trust it, do not read the file
                    continue
                digest = file_hash(path, kind)
            except Exception as exc:  # noqa: BLE001 - locked, online-only or vanished file
                _record_error(conn, rel, kind, "", 0.0, old["id"] if old else None, exc, report)
                continue
            if old is not None and old["sha256"] == digest and old["status"] != "error" and not retry:
                with conn:  # touched but identical: remember the new time so it is not hashed again
                    conn.execute("UPDATE files SET mtime = ? WHERE id = ?", (mtime, old["id"]))
                report.skipped += 1
                continue
            pending.append((path, kind, rel, digest, old))
        reasons = _summarize_pending(kb, summarizer, pending, progress) if summarizer else {}
        fallback = NOT_GENERATED if summarizer else SUMMARIES_OFF
        progress.phase("indexing", total=len(pending))
        records = _ImageBatches(kb, [path for path, kind, *_ in pending if kind == "image"])
        for done, (path, kind, rel, digest, old) in enumerate(pending):
            progress.update(done, rel)
            _index_one(conn, kb, embedder, path, kind, digest, old["id"] if old else None, report,
                       record=records.get(path) if kind == "image" else None,
                       pending_reason=reasons.get(rel, fallback))
        for rel, row in known.items():
            if rel not in seen:
                with conn:
                    remove_file(conn, row["id"])
                report.removed += 1
        progress.phase("linking")
        with conn:
            rebuild_links(conn)
        set_index_state(conn, "complete")
    finally:
        conn.close()
    return report
```

In `rebuild_links`, link people only from transcript and document text, never from generated summaries. Change the chunk source query:

```python
    sources = [("chunk", r["id"], normalize(r["text"]))
               for r in conn.execute("SELECT id, text FROM chunks WHERE kind = 'text'")]  # never generated text
```

- [ ] **Step 5: Cite the moment in transcript conflicts**

In `src/genea/tools/conflicts.py::_document_mismatches`, select `c.start_s` and pass it to the source:

```python
    passages = conn.execute(
        "SELECT c.text, c.page, c.chunk_no, c.start_s, f.path FROM links l "
        "JOIN chunks c ON l.source_kind = 'chunk' AND c.id = l.source_id "
        "JOIN files f ON f.id = c.file_id "
        "WHERE l.person_ged_file = ? AND l.person_xref = ? AND l.confidence >= 1.0 "
        "ORDER BY f.path, c.chunk_no", (ged, xref)).fetchall()
```

```python
            sources = [SourceRef(file=passage["path"], page=passage["page"], chunk=passage["chunk_no"],
                                 start_s=passage["start_s"])]
```

- [ ] **Step 6: Run the whole suite to verify it passes**

Run: `uv run pytest -q`
Expected: PASS (existing indexer, search, links, conflicts and progress tests too: `FakeEmbedder`-only runs pass no summarizer, and the sample KB has no transcript)

- [ ] **Step 7: Commit**

```bash
git add src/genea tests
git commit -m "$(cat <<'EOF'
Index transcript summaries, cached and retried when they fail

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Progress phase and the `genea index` command

**Files:**
- Modify: `src/genea/index/progress.py` (`IndexProgress.phase`, `eta_seconds`, `describe`)
- Modify: `src/genea/cli/common.py` (`get_summarizer`)
- Modify: `src/genea/cli/indexcmds.py` (`--no-summaries`, bar text)
- Modify: `tests/test_progress.py`, `tests/test_transcript_indexing.py`, `tests/test_cli_index.py` (append)

**Interfaces:**
- Consumes: `index_kb(..., summarizer=...)` (Task 7); `make_summarizer` (Task 5).
- Produces: `IndexProgress.phase` may be `"summarizing"`; `describe()` → `summarizing transcripts: 2 / 5, ETA ~15 min, now: docs/a.txt`; `common.get_summarizer(kb) -> Summarizer | None`; CLI flag `genea index --no-summaries`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_progress.py`:

```python
def test_describe_and_eta_while_summarizing():
    now = datetime.fromisoformat("2026-10-08T10:10:00")  # 2 of 5 transcripts in 10 minutes
    progress = _progress(phase="summarizing", done=2, total=5, current="docs/a.txt")
    assert progress.describe(now) == "summarizing transcripts: 2 / 5, ETA ~15 min, now: docs/a.txt"
    assert progress.eta_seconds(now) == pytest.approx(15 * 60)
```

Append to `tests/test_transcript_indexing.py`:

```python
def test_summarizing_is_a_progress_phase_before_indexing(kb):
    from genea.index.progress import read_progress

    write_transcript(kb.root)
    seen = []

    def record(progress):
        on_disk = read_progress(kb)
        seen.append((progress.phase, progress.done, progress.total, progress.current, on_disk and on_disk.phase))

    index_kb(kb, FakeEmbedder(), on_progress=record, summarizer=FakeSummarizer())
    phases = [phase for phase, *_ in seen]
    assert phases.index("scanning") < phases.index("summarizing") < phases.index("indexing") < phases.index("linking")
    summarizing = [(d, t, c) for phase, d, t, c, _ in seen if phase == "summarizing"]
    assert summarizing[0] == (0, 1, None) and (0, 1, REL) in summarizing  # the phase starts, then names the file
    assert all(phase == disk for phase, _, _, _, disk in seen if phase != "scanning")


def test_no_summarizing_phase_when_nothing_needs_a_summary(kb):
    seen = []
    index_kb(kb, FakeEmbedder(), on_progress=lambda p: seen.append(p.phase), summarizer=FakeSummarizer())
    assert "summarizing" not in seen
```

Append to `tests/test_cli_index.py` (add `from fixtures.transcripts import write_transcript` and `from genea.ingest.summarize import FakeSummarizer` to the imports):

```python
def test_index_summarizes_transcripts_unless_told_not_to(sample_root, monkeypatch):
    _prepare(sample_root, monkeypatch)
    write_transcript(sample_root)
    fake = FakeSummarizer()
    monkeypatch.setattr("genea.cli.common.get_summarizer", lambda kb: fake)

    result = runner.invoke(app, ["--kb", str(sample_root), "index", "--no-summaries"])
    assert result.exit_code == 0, result.output
    assert fake.calls == 0
    status = runner.invoke(app, ["--kb", str(sample_root), "status"])
    assert "disabled" in status.output  # single words: rich may wrap a longer phrase across lines

    result = runner.invoke(app, ["--kb", str(sample_root), "index"])
    assert result.exit_code == 0, result.output
    assert fake.calls == 1
    assert "pending" not in runner.invoke(app, ["--kb", str(sample_root), "status"]).output


def test_index_says_when_there_is_no_model_for_summaries(sample_root, monkeypatch):
    _prepare(sample_root, monkeypatch)
    monkeypatch.setattr("genea.cli.common.get_summarizer", lambda kb: None)
    result = runner.invoke(app, ["--kb", str(sample_root), "index"])
    assert result.exit_code == 0, result.output
    assert "summary_model" in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_progress.py tests/test_transcript_indexing.py tests/test_cli_index.py -v`
Expected: FAIL (`describe` returns `2 / 5 files (40 %)...`; `No such option: --no-summaries`; `AttributeError: ... 'get_summarizer'`)

- [ ] **Step 3: Write the implementation**

`src/genea/index/progress.py`: change the `phase` annotation to
`phase: Literal["scanning", "indexing", "summarizing", "linking"]`, then

```python
    def eta_seconds(self, now: datetime | None = None) -> float | None:
        if self.phase not in ("indexing", "summarizing") or self.done <= 0 or self.done >= self.total:
            return None
        elapsed = ((now or datetime.now()) - datetime.fromisoformat(self.phase_started_at)).total_seconds()
        return max(elapsed, 0.0) / self.done * (self.total - self.done)

    def describe(self, now: datetime | None = None) -> str:
        if self.phase == "scanning":
            return f"scanning files ({self.done} found)"
        if self.phase == "linking":
            return "linking people to documents and photos (almost done)"
        if self.phase == "summarizing":
            text = f"summarizing transcripts: {self.done} / {self.total}"
        else:
            text = f"{self.done} / {self.total} files ({self.done * 100 // max(self.total, 1)} %)"
        eta = self.eta_seconds(now)
        if eta is not None:
            text += f", ETA {_duration(eta)}"
        if self.current:
            text += f", now: {self.current}"
        return text
```

`src/genea/cli/common.py`: add `from genea.ingest.summarize import Summarizer, make_summarizer` and

```python
def get_summarizer(kb: KnowledgeBase) -> Summarizer | None:
    return make_summarizer(kb.settings)
```

`src/genea/cli/indexcmds.py`: add the option and the notice, and a bar branch.

```python
@app.command()
def index(
    ctx: typer.Context,
    rebuild: bool = typer.Option(False, "--rebuild", help="Delete the index and build it from scratch."),
    no_summaries: bool = typer.Option(False, "--no-summaries",
                                      help="Do not summarize transcripts in this run (a later run adds them)."),
) -> None:
    """Index new and changed files of the knowledge base."""
    kb = common.open_kb(ctx)
    summarizer = None if no_summaries else common.get_summarizer(kb)
    if summarizer is None and not no_summaries:
        common.console.print("[yellow]Transcript summaries are off: set summary_model in config.toml "
                             "(an Ollama model name).[/yellow]")
```

and in `show`:

```python
            elif p.phase == "summarizing":
                bar.update(task, total=p.total, completed=p.done, description="summarizing " + escape(_short(p.current)))
```

(placed before the final `else`), and pass the summarizer:
`report = index_kb(kb, common.get_embedder(kb), rebuild=rebuild, on_progress=show, summarizer=summarizer)`.

- [ ] **Step 4: Run the whole suite to verify it passes**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/genea tests
git commit -m "$(cat <<'EOF'
Show summarizing progress and add genea index --no-summaries

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Evidence rule, README and a live check

**Files:**
- Modify: `src/genea/agent/prompt.py`
- Modify: `tests/test_prompt.py`
- Modify: `README.md`
- Create: `tests/live/test_live_summary.py`

**Interfaces:**
- Consumes: everything above.
- Produces: rule about summary hits in `SYSTEM_PROMPT` (rule 6) and `MCP_INSTRUCTIONS` (rule 7); transcript citation format in rule 1; README documentation.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_prompt.py`:

```python
def test_summary_hits_are_navigation_not_evidence():
    for prompt in (SYSTEM_PROMPT, MCP_INSTRUCTIONS):
        assert 'kind "summary"' in prompt and "Never cite a summary" in prompt
        assert "@14:20]" in prompt  # how a moment in a recording is cited
    assert "\n6. Search hits" in SYSTEM_PROMPT
    assert "\n7. Search hits" in MCP_INSTRUCTIONS
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_prompt.py -v`
Expected: FAIL (`assert 'kind "summary"' in ...`)

- [ ] **Step 3: Write the implementation**

In `src/genea/agent/prompt.py` replace rule 1's citation sentence:

```python
1. Every genealogical fact you state must have a citation copied from tool results, written like
   [tree.ged:L1042] (GEDCOM line), [docs/matrika.pdf p.3] (document page), [recordings/x.txt @14:20]
   (a moment in a recording transcript) or [photos/x.jpg].
   If the tools return nothing relevant, say so. Never use outside knowledge about these people.
```

Add the rule template and use it in both prompts:

```python
_SUMMARY_RULE = """\
{n}. Search hits with kind "summary" are generated overviews of recordings, not evidence. Use them to decide
   where to look, then read the recording with read_document (start_s, in seconds) and cite what it says,
   like [recordings/x.txt @14:20]. Never cite a summary.
"""

SYSTEM_PROMPT = _HEAD + _AGENT_RULE_4 + _TAIL + _SUMMARY_RULE.format(n=6)
MCP_INSTRUCTIONS = _HEAD + _MCP_RULE_4 + _TAIL + _MCP_RULE_6 + _SUMMARY_RULE.format(n=7)
```

`README.md`: in the Commands table change the index row to
``| `genea init` / `index [--rebuild] [--no-summaries]` / `status` | Set up, index, and inspect a knowledge base |``;
add the configuration row after `exiftool`:

```
| `summary_model` | `""` | Ollama model for transcript summaries (empty: the chat model, if it is an `ollama:` one) |
```

and a new section before "## Privacy":

```markdown
## Transcripts

A `.txt` file made of Whisper segments (`start<TAB>end<TAB> text`) is indexed as a recording: chunks keep
their time in the recording, and answers cite a moment, like `[podcast_family/2022_05_10-x.txt @14:20]`.
`read_document` with `start_s` reads a transcript from that second onward.

`genea index` also asks `summary_model` (through Ollama) for a summary of each recording and of each
section of about 12,000 characters. Summaries are stored in `.genea/summaries/`, one file per transcript,
so `index --rebuild` and model changes do not run the LLM again; a summary is made again only when the
transcript changes or the cache file is deleted and the transcript is re-indexed. Summaries help to find the right recording and moment; the agent is told to
read the transcript and cite that, never the summary. The first run over many long recordings takes a
while; `genea index --no-summaries` skips this step, and a later `genea index` fills the summaries in.
If Ollama is not running, transcripts are still indexed and searchable, and the summaries are retried on
the next run (`genea status` lists them as `summary pending`).
```

- [ ] **Step 4: Add the live check**

Create `tests/live/test_live_summary.py` (needs a real Ollama model; it is skipped by default like the other live tests):

```python
"""A real model summarizes a short transcript: run with `uv run pytest -m live tests/live/test_live_summary.py`."""

import pytest
from fixtures.transcripts import whisper

from genea.config import Settings
from genea.doctor import _has_model, ollama_models
from genea.ingest.summarize import make_summarizer
from genea.ingest.transcript import parse_transcript

pytestmark = pytest.mark.live

ROWS = [
    (0.0, 6.0, "Takže vlastne v 54. sa zobrali v novembri a nasťahovali sa k babke do Prískupíc."),
    (6.0, 12.0, "Babka Fábiková bola vtedy už vdova a býval s nimi aj ujo Juro."),
    (12.0, 18.0, "V 53. zomrel dedo a v 54. sa brali moji rodičia."),
]


def test_a_real_model_summarizes_a_short_transcript():
    summarizer = make_summarizer(Settings())
    names = ollama_models(Settings().ollama_url)
    if summarizer is None or names is None or not _has_model(names, summarizer.model):
        pytest.skip("Ollama is not running or the summary model is not pulled")
    summary = summarizer.summarize(parse_transcript(whisper(ROWS)))
    assert summary.overall.strip()
    assert len(summary.sections) == 1 and summary.sections[0].end_s == 18.0
    assert any("1954" in year or "54" in year for year in summary.sections[0].years) or "54" in summary.overall
```

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS (the live test is deselected by `-m 'not live'`)

- [ ] **Step 6: Commit**

```bash
git add src/genea/agent/prompt.py tests/test_prompt.py tests/live/test_live_summary.py README.md
git commit -m "$(cat <<'EOF'
Tell the agent summaries are not evidence and document transcripts

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review notes (already applied)

- **Spec coverage:** detection and parsing (Task 1); chunking, `DocExtract.segments` (Task 2); schema 2, header in FTS/embedding only, `SchemaMismatch` for old index (Task 3); `SourceRef.cite`, `DocHit`, `read_document(start_s)` and wrappers (Task 4); `Summarizer`, Ollama map-reduce, `summary_model` (Task 5); cache keyed by sha256 with `SUMMARY_VERSION` and atomic write (Task 6); summary chunks, pre-pass, pending marker, retry, no-summarizer marker, `reindex_file` uses the cache only because it passes no reason and no summarizer (Task 7); `summarizing` phase and `--no-summaries` (Task 8); rule 7 for summaries, README, live test (Task 9). The spec's "Open items" (section size, pause threshold) are the constants `SECTION_CHARS`, `PAUSE_SECONDS`.
- **Second review (Opus):** the transcript window budget is measured as escaped JSON instead of a fixed estimate, and the same budget now also fixes long ordinary documents (measured: a 32,000-character `.txt` reached the model as 2,001 characters); summary chunks no longer create person links, because `find_conflicts` treats linked passages' years as document evidence; transcript conflicts cite the moment; the Ollama request sets `num_ctx`.
- **Additions to the spec made in this plan:** `DocumentText.next_start_s` (so a model can continue reading without parsing the text), `SummaryUnavailable` (stop after the first connection failure), `make_summarizer` returns `None` when no local model is configured (the index then runs without summaries and says so), and `--resummarize` is intentionally absent (decided with the user).
- **Known behavior, as agreed:** deleting a cache file or bumping `SUMMARY_VERSION` takes effect for a transcript only when that transcript is re-indexed (changed file, `--rebuild`, or `summary pending`).
