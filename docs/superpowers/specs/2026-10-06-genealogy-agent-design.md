# Genealogy Knowledge-Base Agent ("genea") — Design

Date: 2026-10-06
Status: Approved design, pending spec review

## 1. Goal

A local, open-source agent that works on top of a genealogy knowledge base (KB) —
GEDCOM family trees, text documents, and image metadata — and acts as a **curator**:
it answers questions with citations, reasons across sources (conflicts, timelines,
suggestions), writes research notes, and **proposes** changes that the user reviews
and applies. The same agent works with any KB by pointing it at a root folder.

### Decisions made during brainstorming

| Topic | Decision |
|---|---|
| Role | Curator: Q&A + research + proposing edits |
| LLM | Pluggable; local (Ollama) by default, cloud (e.g. Claude) via config |
| Interface | Core library + CLI first; web UI and MCP adapter later (MCP adapter: see `2026-10-08-mcp-server-design.md`) |
| Data in scope | GEDCOM, text documents (txt/md/docx/pdf), image metadata (EXIF/IPTC/XMP, `.xmp` sidecars) |
| Images | Metadata only; no image-content analysis |
| Write-back | v1: notes (direct) + image XMP sidecars (after approval); GEDCOM changes exported as patch/checklist. Direct `.ged` writes designed for, enabled in v2 |
| Architecture | Hybrid agentic: structured GEDCOM tables + hybrid document search + image metadata table, exposed as tools to an LLM agent |

### Non-goals (v1)

OCR, image content analysis and face recognition, direct `.ged` writes, the web UI,
the MCP adapter, multi-user use, and syncing with online trees.

## 2. Technology stack

| Concern | Choice |
|---|---|
| Language / tooling | Python 3.12, uv |
| Agent framework | PydanticAI (MIT) |
| Local model runtime | Ollama (MIT) |
| Chat model (local default) | Qwen3 8B or 14B (Apache 2.0), depending on GPU memory |
| Chat model (cloud, optional) | Anthropic Claude through the API (paid); installed with the optional `cloud` extra |
| Embeddings | bge-m3 via Ollama (multilingual, 1024-dim); always local |
| Storage + search | SQLite + FTS5 + sqlite-vec |
| GEDCOM | Custom lossless line-tree parser + date parser + mapper |
| PDF / DOCX | pypdf (BSD), python-docx (MIT). PyMuPDF avoided (AGPL) |
| Image metadata | ExifTool (external binary), called through `subprocess` with a UTF-8 argument file (pyexiftool's persistent `-stay_open` mode hung against the Chocolatey shim during probing) |
| CLI | Typer + Rich |
| Config | TOML: global `~/.genea/config.toml`, per-KB `<kb>/.genea/config.toml` |
| Tests | pytest; PydanticAI `TestModel`/`FunctionModel` for agent tests |
| Later | FastAPI web UI, official MCP Python SDK adapter (MCP adapter: see `2026-10-08-mcp-server-design.md`) |

External installs (not pip): **Ollama**, **ExifTool**. `genea doctor` checks for both.

## 3. Architecture

```
genea/
├── kb/          KnowledgeBase: resolves root, owns .genea/ (config, index.db, changes/, notes/, backups/, logs/)
├── ingest/
│   ├── gedcom/  lossless parser · date parser · mapper (lines → persons/families/events)
│   ├── docs/    txt/md/docx/pdf → text chunks (scanned PDFs flagged needs_ocr)
│   └── images/  ExifTool → image records (+ .xmp sidecar)
├── index/       SQLite schema, FTS5 + sqlite-vec, incremental re-index by file hash
├── tools/       Pure query/curate functions (no LLM)
├── agent/       PydanticAI agent: system prompt, tool wiring, model provider from config
├── changes/     Change sets: propose → review → apply/reject/undo; backups; GEDCOM patch export
└── cli/         Typer commands
```

Boundaries:
- `tools/` has no LLM dependency; it is callable from the CLI and tests (and later MCP/web).
- `agent/` only wires tools to the model and selects the provider.
- All writes to user data go through `changes/`. The agent cannot modify source files directly.

### KB layout

```
<kb-root>/
├── ... user data (read-only to the agent) ...
└── .genea/
    ├── config.toml   optional overrides (model, language, include/exclude globs)
    ├── index.db      rebuildable cache
    ├── notes/        agent research notes (Markdown)
    ├── changes/      pending/ applied/ rejected/ stale/ + log.jsonl
    ├── backups/      <timestamp>/... copies taken before apply
    └── logs/         failed-agent-run transcripts
```

### KB selection

`--kb <path|name>` > env `GENEA_KB` > `default_kb` in the global config. Named KBs live
in the global config:

```toml
default_kb = "novak"
[kbs]
novak = 'D:\Genealogy\Novak'
```

Config precedence: CLI flag > per-KB config > global config > built-in defaults.

## 4. Data model (`index.db`)

Every record carries a source reference: the file path plus a GEDCOM line number, or a
page and chunk number.

| Table | Columns |
|---|---|
| `meta` | key, value (includes `schema_version`) |
| `files` | id, path (relative to root), kind (`ged`/`doc`/`image`), sha256, mtime, indexed_at, status (`ok`/`needs_ocr`/`error`), error_msg |
| `persons` | id, file_id, ged_file, xref, given, surname, sex, name_norm, names_norm (all variants, normalized), name_variants (JSON), notes, line_no |
| `families` | id, ged_file, xref, husband_xref, wife_xref, line_no |
| `family_children` | ged_file, family_xref, child_xref, pedigree |
| `events` | id, file_id, ged_file, owner_xref, type, description (e.g. OCCU value, EVEN TYPE), date_raw, date_from, date_to, date_qualifier, place, source_citations (JSON), line_no |
| `chunks` | id, file_id, page, chunk_no, text; plus `chunks_fts` (FTS5) and `chunks_vec` (sqlite-vec, 1024 floats) |
| `images` | id, file_id, sidecar_path, title, description, date_taken, keywords (JSON), persons_shown (JSON), place, gps_lat, gps_lon, search_norm (normalized text of all fields) |
| `links` | source_kind, source_id, person_ged_file, person_xref, confidence |

A person is identified by (`ged_file`, `xref`). In tool APIs this becomes the string
`"<ged_file>#<xref>"`, e.g. `tree.ged#@I12@`.

### Ingestion rules

- **GEDCOM parsing:** a tolerant, lossless line tree that keeps the level, xref, tag, value
  and the exact original line text. Writing the tree back out gives byte-identical output.
  Unknown tags are preserved. Encoding is detected in this order: BOM / `CHAR` header
  (UTF-8, ANSEL) → fallback CP1250. Malformed lines become warnings with line numbers.
- **Dates:** `ABT X` → [X−5, X+5]; `EST X` → [X−5, X+5]; `BEF X` → [X−100, X];
  `AFT X` → [X, X+100]; `BET X AND Y` / `FROM X TO Y` → [X, Y]; an exact date →
  [d, d]. Ranges are stored as ISO dates (year-only dates become Jan 1 – Dec 31).
  Dual dates and calendar escapes (`@#DJULIAN@` etc.) are kept in `date_raw`, and the
  range is a best effort. Dates that cannot be parsed get NULL range columns.
- **Names:** `name_norm` = lower-cased, accents stripped (`Novák` → `novak`).
  Variants come from all `NAME` records.
- **Documents:** chunks of about 800 tokens with a 100-token overlap, keeping PDF page
  numbers. A PDF page with no extractable text → the file is marked `needs_ocr`
  (if every page is empty) and the empty pages are skipped.
- **Images:** the sidecar is `<stem>.xmp` (Lightroom style) or `<name>.<ext>.xmp` (darktable
  style), whichever exists; new sidecars use `<stem>.xmp`. The image hash used for
  incremental indexing covers the image and its sidecar.
  ExifTool reads the embedded data and the `.xmp` sidecar (the sidecar wins
  on conflicts). Fields are mapped from the common EXIF/IPTC/XMP tags
  (DateTimeOriginal, XMP-dc:Title/Description/Subject, IPTC Keywords,
  XMP-iptcExt:PersonInImage, location fields, GPS).
- **Links:** after ingestion, document chunks and image keywords/persons are matched
  against person `name_norm` and its variants (full given name + surname). Each match
  gets a confidence score (1.0 for a full name match in either order, e.g. "Jan Novák" or
  "Novák Jan"; 0.6 for an initial plus surname, e.g. "J. Novák"). Links are rebuilt after
  every index run.
- **Incremental re-indexing:** unchanged sha256 → skip. Changed → delete that file's rows
  and re-ingest it. Missing → delete its rows. A changed GEDCOM is re-imported in full.
  `--rebuild` deletes `index.db` and starts over.
- **Scope:** every file under the root except `.genea/` and the configured exclude globs.
  Extensions: `.ged`; `.txt .md .docx .pdf`; `.jpg .jpeg .tif .tiff .png .heic`.

### Search

`search_documents`: an FTS5 BM25 search (top 30) and a vector search (top 30) are
merged with reciprocal rank fusion (k = 60), and the top k chunks are returned
(default k = 8).

## 5. Tools and agent

All tools return Pydantic models, and every result includes `sources: list[SourceRef]`
(`file`, optional `line`, `page`, `chunk`).

| Tool | Behaviour |
|---|---|
| `find_person(name, born_around?, place?)` | Matches on normalized names and variants; ranks by birth-year proximity and place; returns up to 10 candidates with life dates |
| `get_person(person_id)` | Names, events, families (spouses and children), notes, citations |
| `relatives(person_id, relation, depth=1)` | relation ∈ parents, children, siblings, spouses, ancestors, descendants; depth ≤ 6 |
| `timeline(person_id)` | The person's events plus births, marriages and deaths of parents, spouses and children, sorted by `date_from` |
| `search_documents(query, k=8)` | Hybrid search (see above) |
| `read_document(path, page?)` | Full text (up to 12k chars, truncated with a marker); the path must be inside the root and not in `.genea/` |
| `find_images(person?, place?, year_from?, year_to?, text?)` | Searches the images table; when `person` is a person ID, also uses `links` |
| `find_conflicts(person_id?)` | Rule checks (child born when a parent was under 12, or the mother over 55; events after death; baptism more than a year before birth; marriage before age 12). Also flags document chunks linked to the person that mention a year differing from a GEDCOM event, as `needs_review` |
| `write_note(title, markdown, related_persons)` | Writes `.genea/notes/<date>-<slug>.md` with front matter; applied immediately |
| `propose_change(target, operation, payload, rationale, sources)` | Validates and stores a pending change (see section 6) |

### Agent

- PydanticAI `Agent` with the tools above. The model string comes from config, e.g.
  `ollama:qwen3:14b` or `anthropic:claude-sonnet-5-5`.
- System prompt rules:
  1. Cite a source for every genealogical fact (`[tree.ged:L1042]`, `[docs/x.pdf p.3]`).
  2. If a person is ambiguous, call `find_person` and ask the user instead of guessing.
  3. Separate *Documented* from *Inferred*.
  4. Never claim an edit was made; report only proposed change IDs.
  5. Answer in the language of the question.
- Guardrails: at most 15 tool calls per question, 2 retries on tool-argument validation
  failures, tool outputs truncated to fit a configurable token budget.
- Privacy: with a non-local provider, a one-time notice per session
  (`privacy_notice = false` turns it off).
- The embedding model is configured separately from the chat model and is always local.

## 6. Change sets

The change file is `.genea/changes/<status>/<id>.json`:

```json
{
  "id": "chg-20261006-0007",
  "created": "2026-10-06T14:02:11",
  "target": {"kind": "image_xmp", "path": "photos/1932_wedding.jpg"},
  "operation": "set_fields",
  "payload": {"XMP-dc:Description": "..."},
  "rationale": "...",
  "sources": [{"file": "tree.ged", "line": 1042}],
  "base_hash": "<sha256 of the target sidecar (or of the image when there is no sidecar) at proposal time>",
  "status": "pending"
}
```

Targets and operations:
- `image_xmp`: `set_fields`, `add_keywords`, `remove_keywords`. Written only to the
  `<image>.xmp` sidecar (created if missing). The original image is never modified.
  Allowed field keys come from a whitelist of XMP-dc / XMP-iptcExt / XMP-photoshop tags.
- `gedcom`: `add_event`, `update_event`, `add_note`, `add_source_citation`,
  `link_child`, `add_person`. In v1 these are export-only:
  `genea changes export` writes `.genea/exports/<date>-checklist.md` (human steps)
  and `.genea/exports/<date>-changes.ged` (a GEDCOM fragment). The `apply()` code path
  exists behind the config flag `gedcom_direct_write = false` and refuses to run in v1.

Commands:
- `genea changes list [--status]`
- `genea changes show <id>`: rationale, sources and a coloured before/after diff
- `genea changes apply <id> --manual`: marks a GEDCOM change as done by hand in your
  genealogy program (no file is written)
- `genea changes apply <id>|--all`: verify `base_hash` (a mismatch moves the change to
  `stale/`) → back up to `.genea/backups/<ts>/` → write with ExifTool → move to
  `applied/` → re-index the file
- `genea changes reject <id> [--reason]`
- `genea changes undo <id>`: restore from backup, then move to `rejected/` with reason `undone`
- `genea changes export`

Every apply, reject and undo adds a line to `.genea/changes/log.jsonl`. `propose_change`
returns an existing identical pending or rejected change (same target, operation and
payload) instead of creating a duplicate.

## 7. CLI

```
genea doctor
genea init [--kb]                 create .genea/ with a default config
genea index [--rebuild]
genea status                      index summary + problem files
genea ask "<question>" [--show-tools]
genea chat [--show-tools]
genea person <query|id>           direct tool access (no LLM)
genea conflicts [<person_id>]     direct tool access (no LLM)
genea changes list|show|apply|reject|undo|export
```

Global option: `--kb <path|name>`.

## 8. Error handling

- Indexing handles each file separately: errors are recorded in `files` and the run
  continues. A summary is printed (ok / needs_ocr / error); `genea status` lists the problems.
- GEDCOM: tolerant parsing; warnings with line numbers.
- Missing Ollama, model or ExifTool: an actionable message; `genea doctor` checks everything up front.
- Agent: 2 validation retries; on final failure, a clean error plus a transcript in `.genea/logs/`.
- A `schema_version` mismatch prompts `genea index --rebuild`.
- Change application: `base_hash` check, backup, undo.

## 9. Testing

- Fixture `tests/fixtures/kb_sample/`: a GEDCOM of about 20 people over 4 generations
  (accented names, ABT/BET dates, one deliberate conflict); txt/md/docx/pdf files
  (one PDF without a text layer); three JPEGs with EXIF/IPTC/XMP.
- Unit tests: lossless GEDCOM round trip (also run on files in the git-ignored
  `tests/private/`), table-driven date parsing, encodings, chunking, name
  normalization, and each tool against the indexed sample.
- Change-set tests: propose → apply → verify the sidecar → undo; stale detection;
  de-duplication; export output.
- Agent tests with `TestModel`/`FunctionModel` (no real LLM).
- Optional `pytest -m live`: about 15 Q/A pairs against the sample KB with a real model,
  for comparing models.
- Embeddings in non-live tests use a deterministic fake embedder.

### v1 acceptance criteria (on the user's real KB)

1. `genea doctor` passes.
2. `genea --kb <path> index` completes and prints a summary.
3. `genea ask "who were <person>'s grandparents?"` answers correctly with GEDCOM citations.
4. A proposed image caption change can be listed, shown, applied and undone.

## 10. Known issues and follow-ups

### Keyword changes drop embedded IPTC-only keywords from the index

Found in the final whole-branch review of the v1 implementation. **Fixed** on `jrumanov/keyword-defect-fix`:
`apply_change` computes the image's effective keyword list (`effective_keywords`), applies the add or
remove, and writes the whole list to the sidecar; `describe_change` uses the same list for "before".
The related remove-with-no-sidecar failure is fixed by the same change. The two other minors below were fixed afterwards on `jrumanov/parked-minors-fix`: a blank date is rejected
in validation, and blank list items are dropped before ExifTool sees them.

**Problem.** The XMP sidecar is authoritative per field: when it carries `XMP-dc:Subject`,
that list replaces the embedded keywords (the union of the image's `XMP-dc:Subject` and
`IPTC:Keywords`) in the index. A new sidecar is seeded only with
`-tagsfromfile … -xmp:all`, which does not copy IPTC keywords. As a result, `add_keywords`
and `remove_keywords` on a photo whose keywords are IPTC-only (common for older scanned or
tagged photos) leave the index with only the sidecar's list:

- IPTC `[rodina, svatba]`, then `add_keywords [Brno]`: the index shows `['Brno']`.
- IPTC `[rodina, svatba]` plus XMP `[rodina]`, then `add_keywords [Brno]`: `svatba` is lost.
- IPTC-only image with an existing sidecar (for example after a title change), then
  `remove_keywords [rodina]`: the index shows `[]`, because the empty placeholder entry
  that `remove_keywords` leaves hides the embedded keywords too.

`genea changes show` reads only the sidecar's `XMP-dc:Subject`, so its before/after preview
shows `before: []` and hides the loss.

**Impact.** The loss is limited to the index view and the sidecar's keyword list. Originals
and embedded metadata are never modified, and the index can be rebuilt, so no user data is
destroyed. The behaviour surprises the user after they approve a keyword change.

**Required fix.**
- Before applying `add_keywords` or `remove_keywords`, seed the sidecar's `XMP-dc:Subject`
  from the effective (merged) keyword list of the image, then add or remove the requested
  keywords.
- Compute the `changes show` "before" value the same way, from the merged list rather than
  the sidecar alone, so the preview matches what the index will show.
- Add an ExifTool test using an IPTC-only image (add, remove, and add-after-remove) that
  checks both the index contents and the preview.

**Related, lower priority.**
- `remove_keywords` on an IPTC-only image with no sidecar fails with ExifTool "Nothing to
  write" and is rolled back. The same seeding fix resolves it.
- Clearing a date (`XMP-photoshop:DateCreated: ""`) deletes the sidecar date, so the embedded
  `ExifIFD`/IPTC date shows through again, while the preview shows the date going to empty.
  Reject an empty date in validation or document it.
- A payload list that mixes empty and non-empty items (for example `["A", ""]`) deletes the
  tag instead of setting it. Filter empty items before building the ExifTool arguments.
