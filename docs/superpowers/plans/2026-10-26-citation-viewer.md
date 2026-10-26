# Clickable Citations with a Local Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every citation Claude writes through the genea MCP server is a link that opens a local viewer page at the cited page, line, moment or passage.

**Architecture:** A new package `genea.viewer` runs a stdlib `ThreadingHTTPServer` on `127.0.0.1` in a daemon thread, started by `genea mcp` before the MCP server. Tool results get a `link` next to each citation (built from the fields the citation already has, only when a viewer URL is set). The viewer reads the KB read-only: each request opens its own `connect_readonly` connection (the MCP context's connection belongs to the event-loop thread) and finds chunks in re-extracted text, so the index schema does not change.

**Tech Stack:** Python 3.12, stdlib `http.server`/`urllib.parse`/`html`, pydantic `computed_field`, existing `extract_document`, `parse_gedcom`, `connect_readonly`; pytest with `urllib.request` against a server on port 0.

**Spec:** `docs/superpowers/specs/2026-10-26-citation-viewer-design.md`

**Deviation from the spec (tell the user):** slice 2 needs no schema bump and no index rebuild. The offset of a passage inside its chunk is computed at search time from the chunk text, and the viewer locates the chunk inside the re-extracted page text with `str.find`. Two PRs remain: Part A (Tasks 1-6) and Part B (Tasks 7-9). Part A ships on the current branch `jrumanov/citation-viewer` (it already holds the design commit); start Part B from `main` after Part A is merged.

## Global Constraints

- Server binds `127.0.0.1` only; default port setting `viewer_port = 8765`; if taken, bind a free port and use it in all links.
- Read-only: GET only; serves only files listed in the `files` table with status `ok`, resolved with `KnowledgeBase.resolve_inside`; never `.genea`.
- Never serve images whose `keywords` contain (case-insensitive) `travel card`, `resident permit`, `id card`, `driving license`, `health insurance card`.
- Visible citation text is unchanged (`[docs/matrika.pdf p.3]`); only the link target is added.
- Links exist only while the viewer runs; without a base URL (`genea chat`, tests) tool output is byte-for-byte what it is today.
- Results must still fit `tool_output_chars`/`mcp_output_chars` with links included (`search._result_json` already measures `model_dump_json`).
- No audio player, no JavaScript framework, no external assets or CDN; pages work in light and dark (`prefers-color-scheme`).
- Slovak/Czech diacritics, spaces, `#`, `%` in paths must round-trip. Every dynamic string in HTML goes through `html.escape`.
- Repo style: `from __future__ import annotations`, module docstring, short comments only where non-obvious; squash-merge, one commit per PR, Windows-safe pytest ids.

## Review Focus

1. Path with spaces, `#`, `%`, `+` and diacritics (`D:\Foto DB\rodinná história\Šimon #2.md`): link opens the right file (Task 1 test, Task 2 test).
2. File changed since indexing, or chunk not found in the page: page opens unhighlighted with a visible note, no error (Task 3 test).
3. Index missing or still building: viewer shows a notice, never a 500 (Task 2 test).
4. Request with a foreign `Host` header (DNS rebinding) or `..` in the path: 403/404 (Task 2 tests).
5. Unreadable or damaged PDF/docx: readable error page, server keeps running (Task 5 test).

---

## Part A: the viewer and links (PR 1)

### Task 1: Setting and link builder

**Files:**
- Modify: `src/genea/config.py` (add `viewer_port: int = 8765  # localhost port of the citation viewer started by genea mcp`)
- Create: `src/genea/viewer/__init__.py` (docstring only), `src/genea/viewer/links.py`
- Modify: `src/genea/tools/models.py` (`SourceRef`)
- Test: `tests/test_viewer_links.py`, extend `tests/test_config.py`

**Interfaces:**
- Produces: `links.set_base_url(url: str | None) -> None`; `links.get_base_url() -> str | None`; `links.build_link(file: str, *, line: int | None = None, page: int | None = None, chunk: int | None = None, start_s: float | None = None, span: tuple[int, int] | None = None) -> str | None` (None when no base URL); URL shape `{base}/v/{quote(file, safe="/")}` plus query `line`, `page`, `chunk`, `t` (seconds, integer), `o` and `n` (span start and length). `SourceRef.link` is a pydantic computed field returning `build_link(...)` from its own `file/line/page/chunk/start_s`; it is omitted (None) when no base URL is set or when the model is built with `linked=False` (an excluded field `linked: bool = Field(default=True, exclude=True)`).

- [ ] **Step 1: Write the failing tests**
  - `test_no_base_url_means_no_link`: `build_link("a.md") is None` and `SourceRef(file="a.md").model_dump(exclude_none=True) == {"file": "a.md"}`.
  - `test_link_shapes` with base `http://127.0.0.1:8765`: line → `.../v/tree.ged?line=1042`; page → `.../v/docs/matrika.pdf?page=3`; chunk+start_s=842.4 → `.../v/rec/x.txt?chunk=2&t=842`; plain photo → `.../v/photos/x.jpg`.
  - `test_awkward_paths_round_trip`: file `rodinná história/Šimon #2 100%+.md` → `urllib.parse.unquote` of the path part equals the original; `#`, `%`, `?` and space do not appear raw.
  - `test_source_ref_link_in_json`: with base set, `SourceRef(file="docs/a.pdf", page=3).model_dump(exclude_none=True)["link"].endswith("/v/docs/a.pdf?page=3")`; with `linked=False` no `link` key.
  - `test_viewer_port_setting`: default 8765, overridable from the KB's config.toml (follow the existing pattern in `tests/test_config.py`).
  - A fixture (autouse in this file, and exported for other viewer tests via `tests/conftest.py`) resets `set_base_url(None)` after each test.
- [ ] **Step 2: Run** `uv run pytest tests/test_viewer_links.py tests/test_config.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** `links.py` with a module-level base URL (strip trailing `/`), the `SourceRef.link` computed field and the `linked` flag.
- [ ] **Step 4: Run** the same command → PASS; also `uv run pytest tests -q` → nothing else changed (link absent without base).
- [ ] **Step 5: Commit** `git add -A && git commit -m "Add viewer link builder and viewer_port setting"` (Part A is squashed at merge).

### Task 2: Server skeleton, safety rules, raw files

**Files:**
- Create: `src/genea/viewer/server.py`, `src/genea/viewer/policy.py`
- Test: `tests/test_viewer_server.py`

**Interfaces:**
- Produces: `policy.EXCLUDED_TAGS: frozenset[str]`; `policy.is_excluded(keywords: Iterable[str]) -> bool`; `server.start_viewer(kb: KnowledgeBase, port: int | None = None) -> ViewerHandle` where `ViewerHandle` has `.url: str` (e.g. `http://127.0.0.1:8765`) and `.close() -> None`; `port=None` uses `kb.settings.viewer_port`, `0` means any free port. Routes: `GET /v/<rel path>` (a page, built by `pages.render(kb, conn, rel, params)`; this task returns a placeholder body "viewer" for known files), `GET /raw/<rel path>` (file bytes with a guessed content type, `Content-Disposition: inline`). Helper `server.lookup(kb, conn, rel) -> FileRow | None` (`id, path, kind, status`) used by later tasks. A request without a usable index gets an HTML notice (status 503) saying the index is missing or being built, `genea index`.
- Consumes: `connect_readonly(kb.index_path)`, `KnowledgeBase.resolve_inside`.

- [ ] **Step 1: Write the failing tests** (sample KB from the `kb` fixture, indexed with `FakeEmbedder` as `tests/test_mcp_server.py` does; server started with `start_viewer(kb, port=0)`, requests via `urllib.request`):
  - `test_binds_to_loopback_only`: the socket's host is `127.0.0.1`.
  - `test_port_in_use_falls_back`: start two viewers on the same explicit port; the second `.url` has a different port and works.
  - `test_raw_serves_indexed_file`: `/raw/docs/<an indexed pdf or md>` returns 200 with the exact bytes.
  - `test_unindexed_and_hidden_files_are_404`: a file in the KB root that is not in `files`, `/raw/.genea/index.db`, `/raw/../outside.txt` and `/raw/%2e%2e/outside.txt` → 404 (or 403), never the bytes.
  - `test_host_header_must_be_loopback`: request with `Host: evil.example` → 403.
  - `test_post_is_rejected`: POST → 405.
  - `test_excluded_image_is_not_served`: an indexed image whose sidecar/keywords include `Id Card` → `/raw/` and `/v/` both 404, while a normal photo is 200.
  - `test_no_index_gives_notice`: KB without `index.db` → 503 and body mentions `genea index`.
  - `test_server_survives_a_bad_request`: a handler exception returns 500 text and the next request still works.
- [ ] **Step 2: Run** `uv run pytest tests/test_viewer_server.py -q` → FAIL.
- [ ] **Step 3: Implement.** One `BaseHTTPRequestHandler` subclass; validate `Host` against `{127.0.0.1:port, localhost:port}`; path-decode once with `urllib.parse.unquote`, then `kb.resolve_inside`; keep logging silent (override `log_message`). Photos are checked through the `images.keywords` JSON of the file's row.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `Add the localhost citation viewer server`.

### Task 3: Text documents and transcripts

**Files:**
- Create: `src/genea/viewer/pages.py` (shared HTML shell, `render(...)`), `src/genea/viewer/textview.py`
- Modify: `src/genea/viewer/server.py` (route `/v/` to `pages.render`)
- Test: `tests/test_viewer_text.py`

**Interfaces:**
- Produces: `pages.shell(title: str, body: str) -> str` (full HTML document: charset, viewport meta, `:root` colour tokens with a dark `prefers-color-scheme` override, readable measure); `pages.notice(title: str, message: str) -> str`; `pages.render(kb, conn, rel: str, params: dict[str, str]) -> str` dispatching by `files.kind` and suffix; `textview.render_text(kb, conn, rel, params) -> str` for `.md/.txt/.docx` (and the text of PDFs in Task 5); `textview.render_transcript(...)`.
- Behaviour: text: show the page given by `page` (all pages in order when absent; PDFs are Task 5); find the DB chunk `(file, chunk_no)` text inside `page.text.strip()` with `str.find` and wrap it in `<mark id="hit">`; the page ends with a few lines of script-free anchor scrolling (`#hit` in the URL fragment via a `<script>` one-liner `document.getElementById('hit')?.scrollIntoView({block:'center'})`). Transcript: every segment as `[mm:ss] text` (reuse `format_time`); segments inside the chunk's `[start_s, end_s]` (from DB via `chunk`), or the first segment ending after `t` when only `t` is given, are `<mark>`ed and scrolled to; `summary`-kind chunks are not highlighted. If the chunk is missing or not found in the text, no mark and a visible note "The file changed since it was indexed; showing it without highlighting".

- [ ] **Step 1: Write the failing tests** (build docs with the KB fixtures; assert on the returned HTML, via the server for one end-to-end case):
  - `test_markdown_chunk_is_marked`: link with `?chunk=0` on a short md file contains `<mark id="hit">` around the chunk text, HTML-escaped (`<` in the source appears as `&lt;`).
  - `test_changed_file_opens_unmarked_with_note`: rewrite the md after indexing so the chunk text is gone → 200, no `<mark`, note text present.
  - `test_transcript_marks_chunk_range`: transcript fixture from `tests/fixtures/transcripts.py`, `?chunk=1` marks exactly the segments between that chunk's `start_s` and `end_s`; `?t=14` (no chunk) marks the segment containing 14 s.
  - `test_transcript_has_no_audio_element`: `<audio` and `<video` absent.
  - `test_unknown_file_is_404_page` and `test_page_param_on_text_doc_without_pages_is_ignored`.
- [ ] **Step 2: Run** `uv run pytest tests/test_viewer_text.py -q` → FAIL.
- [ ] **Step 3: Implement** the shell, the dispatcher and both renderers.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `Show text documents and transcripts in the viewer`.

### Task 4: GEDCOM records

**Files:**
- Create: `src/genea/viewer/gedview.py`
- Modify: `src/genea/viewer/pages.py` (dispatch `.ged`)
- Test: `tests/test_viewer_ged.py`

**Interfaces:**
- Produces: `gedview.record_lines(doc: GedDocument, line_no: int) -> list[tuple[int, str]]` — `(line_no, display text)` for the level-0 record containing `line_no`, through the line before the next level-0 line; `gedview.render_ged(kb, rel, params) -> str` highlighting `line` (`<mark id="hit">`), showing each line as `level [xref] tag value` decoded with the file's own encoding (ANSEL included).
- Consumes: `parse_gedcom` (`GedDocument.lines`, `GedLine.line_no/level/xref/tag/value`).

- [ ] **Step 1: Write the failing tests** (`tests/fixtures/kb_sample/tree.ged` and an ANSEL variant if the existing ANSEL test fixture helper allows):
  - `test_record_lines_cover_the_record`: for the line of a `BIRT` event of `@I12@`, the result starts at the `0 @I12@ INDI` line and ends before the next `0` line.
  - `test_cited_line_is_highlighted`: HTML has exactly one `<mark id="hit">` and it contains that line's tag.
  - `test_line_outside_file_shows_file_top_with_note`; `test_ansel_diacritics_render`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `Show GEDCOM records in the viewer`.

### Task 5: PDFs and photos

**Files:**
- Create: `src/genea/viewer/pdfview.py`, `src/genea/viewer/photoview.py`
- Modify: `src/genea/viewer/pages.py`
- Test: `tests/test_viewer_pdf_photo.py`

**Interfaces:**
- Produces: `pdfview.render_pdf(kb, conn, rel, params) -> str` — an `<iframe src="/raw/<rel>#page=N">` (the browser's own PDF viewer, which honours the page) above the extracted text of that page with the chunk marked as in Task 3 (reuse `textview` helpers rather than copying them); scanned PDFs (no text layer) show only the iframe plus the note "No text layer: this PDF has not been read as text yet". Extraction errors give `pages.notice` with the message, status 200. `photoview.render_photo(kb, conn, rel) -> str` — `<img src="/raw/<rel>">` and a definition list of the `images` row (title, description, date taken, place, persons shown, keywords); formats a browser cannot show (`.tif`, `.tiff`, `.heic`) show the metadata and a note instead of the image. Excluded photos are handled by Task 2.

- [ ] **Step 1: Write the failing tests:**
  - `test_pdf_page_param_goes_into_iframe_src` (`src` ends with `#page=3`).
  - `test_pdf_chunk_is_marked_in_page_text` (sample PDF from `make_kb`).
  - `test_scanned_pdf_has_note_and_no_mark` (PDF with no text, as `make_kb` already builds one for the needs-OCR case if present; otherwise create with `_pdf` and an empty page).
  - `test_damaged_pdf_gives_error_page_and_server_survives` (`damage_pdf`).
  - `test_photo_shows_metadata_escaped`: a title containing `<b>` is escaped; `<img` src is `/raw/photos/...`.
  - `test_tiff_photo_has_no_img_tag`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `Show PDFs and photos in the viewer`.

### Task 6: Links in tool results, MCP wiring, prompt, docs

**Files:**
- Modify: `src/genea/tools/models.py` (`DocHit.link: str | None = None`, `Mention.link: str | None = None`), `src/genea/tools/search.py` (hits get `link=build_link(file, page=..., chunk=..., start_s=...)`; the `SourceRef`s in `result.sources` are built with `linked=False` so a hit's link is not repeated), `src/genea/tools/mentions.py` (each `Mention` gets `link` with its file, page, chunk, start_s), `src/genea/mcp_server.py` (`serve` starts the viewer and sets the base URL; `build_server(source, ...)` unchanged signature), `src/genea/cli/mcpcmds.py` (stderr line with the viewer URL), `src/genea/agent/prompt.py` (new `_LINK_RULE`, appended to `MCP_INSTRUCTIONS` only), `README.md` (section under "Use with Claude (MCP)").
- Test: extend `tests/test_prompt.py`, `tests/test_mcp_server.py`, `tests/test_cli_mcp.py`; create `tests/test_viewer_results.py`

**Interfaces:**
- Consumes: Task 1 `build_link`/`set_base_url`, Task 2 `start_viewer`.
- Produces: `mcp_server.serve(gate: IndexGate) -> None` additionally starts `start_viewer(gate.kb)`, calls `links.set_base_url(handle.url)` and closes the viewer and clears the base URL in a `finally`. The rule text (rule number continues after the DNA rule): "When a source, hit or mention has a `link`, write its citation as a markdown link with the usual citation text, like [docs/matrika.pdf p.3](link). Copy links exactly; never build or change one."

- [ ] **Step 1: Write the failing tests:**
  - `test_search_hit_has_link_and_sources_do_not`: with base set, `search_documents` JSON: every hit has `link` with its chunk, `sources` entries have no `link`.
  - `test_documents_about_mentions_have_links`; `test_get_person_event_sources_have_links`; `test_find_images_source_has_link`; `test_read_document_source_has_link` (page and start_s variants).
  - `test_output_without_viewer_is_unchanged`: base unset → JSON equals the current output (compare to a golden string produced before the change; keep an existing test or snapshot).
  - `test_links_count_against_the_output_budget`: base set, `tool_output_chars=1500`: `search_documents` result has no `"truncated"` and `len <= 1500`.
  - `test_mcp_instructions_include_link_rule_and_agent_prompt_does_not`.
  - `test_serve_starts_and_stops_viewer`: monkeypatch `build_server(...).run` to return; assert the viewer URL was reachable during the call (`urlopen` of `/raw/...` inside the patched run) and unreachable after, base URL cleared.
  - CLI: `genea mcp` stderr mentions `http://127.0.0.1:` (follow `tests/test_cli_mcp.py` pattern).
- [ ] **Step 2: Run** `uv run pytest tests -q` for those files → FAIL.
- [ ] **Step 3: Implement.** In `search.py` the `width` computation and `_result_json` fitting stay as they are: `link` is a model field, so the existing fit loop already counts it.
- [ ] **Step 4: Run** `uv run pytest tests -q` → all pass.
- [ ] **Step 5: Manual check** (record in the PR): restart Claude Code, ask "find Josef in the documents", click a citation of a PDF, an md, a transcript, a `.ged` line and a photo; confirm each opens correctly.
- [ ] **Step 6: Commit** `Make citations clickable through the local viewer`, then open PR 1 with the `pr-create` skill.

---

## Part B: highlighting the cited sentence (PR 2, from `main` after PR 1 is merged)

### Task 7: Sentence span inside a chunk

**Files:**
- Modify: `src/genea/text.py`
- Test: extend `tests/test_text.py`

**Interfaces:**
- Produces: `text.match_span(text: str, needles: list[str], max_chars: int = 400) -> tuple[int, int] | None` — `(start, end)` offsets into the original `text` of the sentence (bounded by `.`, `?`, `!`, or a blank line) containing the first match of any needle, matched exactly as `snippet` matches (word boundary, prefix rule, accent-insensitive via `_normalize_with_map`); a sentence longer than `max_chars` is cut to a `max_chars` window around the match; `None` when nothing matches.

- [ ] **Step 1: Write failing tests:** match in the middle of a sentence returns the sentence bounds; accent-insensitive (`Šimon` found by `simon`); first of two matches wins; a 2,000-character run-on sentence returns a span of at most 400 characters containing the match; no match → None; span slices back to text containing the needle (`normalize(text[s:e])`).
- [ ] **Step 2: Run** `uv run pytest tests/test_text.py -q` → FAIL. **Step 3: Implement** reusing `_normalize_with_map`. **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `Find the sentence around a search match`.

### Task 8: Hits and mentions carry the span

**Files:**
- Modify: `src/genea/tools/search.py`, `src/genea/tools/mentions.py` (compute `span = match_span(row["text"], needles)` next to the existing `snippet` call and pass `span=` to `build_link`), `src/genea/viewer/links.py` only if `build_link` needs no change (it already supports `span` from Task 1)
- Test: extend `tests/test_viewer_results.py`

**Interfaces:**
- Consumes: Task 1 `build_link(..., span=)` → query `o`, `n`; Task 7 `match_span`.

- [ ] **Step 1: Write failing tests:** a search hit link contains `o=` and `n=` and the offsets slice the chunk text (read from the index) to a sentence containing the query word; a hit with no word match has no `o/n`; `documents_about` mentions likewise; links stay under the budget test from Task 6.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest tests -q` → PASS.
- [ ] **Step 5: Commit** `Put the matched sentence into citation links`.

### Task 9: Viewer highlights the sentence

**Files:**
- Modify: `src/genea/viewer/textview.py`, `src/genea/viewer/pdfview.py` (page text under the iframe)
- Test: extend `tests/test_viewer_text.py`, `tests/test_viewer_pdf_photo.py`; README section updated

**Interfaces:**
- Consumes: query params `o`, `n` (offsets relative to the chunk text). Behaviour: when the chunk is found, the whole chunk gets a light background (`<mark class="chunk">`) and the span inside it a strong one (`<mark id="hit">`, scrolled to); non-integer, negative or out-of-range `o/n` are ignored (chunk-level highlight only). Transcripts ignore `o/n` (a chunk is whole segments).

- [ ] **Step 1: Write failing tests:** `?chunk=0&o=10&n=20` marks exactly `chunk_text[10:30]` with id `hit`, inside the chunk mark; `o=-1`, `o=abc`, `o=999999` fall back to the chunk mark without error; Slovak diacritics inside the span are preserved and escaped; PDF page text behaves the same.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** (build the HTML from three escaped slices). **Step 4: Run** `uv run pytest tests -q` → PASS.
- [ ] **Step 5: Manual check:** ask Claude a question whose answer is in the middle of a long chunk; click; the sentence is the strong highlight.
- [ ] **Step 6: Commit** `Highlight the cited sentence in the viewer`, then open PR 2 with `pr-create`.

---

## Self-review notes

- Spec coverage: links for all citation kinds (Tasks 1, 6), viewers for text/transcript/PDF/photo/GED (3-5), server safety and exclusions (2), starts with MCP (6), slice 2 highlighting (7-9), non-goals respected (no audio, no default-app launching, no persistence). The "no schema bump" deviation is stated at the top.
- Types used across tasks: `build_link`, `set_base_url`, `SourceRef.link/linked`, `start_viewer/ViewerHandle`, `pages.render/shell/notice`, `match_span` are defined once and referenced by the same names.
