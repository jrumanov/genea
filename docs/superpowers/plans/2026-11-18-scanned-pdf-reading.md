# Scanned PDF Reading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PDFs without a text layer readable by Claude Code subagents: list the ones still unread, render their pages to PNG, document the recipe in the extract skills, and stop reporting a PDF as a problem once it has a sibling `.md`.

**Architecture:** One small module `genea/ingest/scans.py` holds the sibling-`.md` rule, the list of unread scans and the page renderer. Two thin CLI commands call it. `index_summary` uses the same rule to filter `needs_ocr` problems. The indexer itself is unchanged: it already indexes the `.md` the subagent writes.

**Tech Stack:** Python 3.12, typer, pypdfium2 (new optional extra `scan`), pytest, reportlab (test fixtures).

**Spec:** `docs/superpowers/specs/2026-11-18-scanned-pdf-reading-design.md`

## Global Constraints

- No local OCR and no Claude API calls from genea code.
- Rendering: about 200 DPI, one `page-001.png` per page, written only to a folder the caller names; never into the KB.
- Never overwrite an existing `.md`; the review line `Review status: NOT REVIEWED (machine transcription, check every value against the image)` stays; skills never write "reviewed".
- The `.md` sits next to the PDF as `<pdf stem>.md` with `### Page N` sections.
- pypdfium2 goes in a new extra `scan` (like `transcribe`) and in the `dev` dependency group.
- No schema change, no viewer change.

## Review Focus

- Uppercase `.PDF` extension: the sibling `.md` is still found (`x.PDF` -> `x.md`).
- A damaged or password-protected PDF: `render-pdf` prints a red one-line error and exits 1, no traceback.
- Output folder already holds `page-001.png` from an earlier run: it is replaced, other files are left alone.
- Argument is not a PDF or does not exist: exit 2 with a clear message.
- A `.md` that exists but is not indexed yet: `genea scans` (filesystem check) already treats the PDF as covered; `genea status` follows after the next `genea index`.

---

### Task 1: Sibling-md rule, unread-scan list and status filter

**Files:**
- Create: `src/genea/ingest/scans.py`
- Modify: `src/genea/index/status.py:44-45`
- Test: `tests/test_scans.py`

**Interfaces:**
- Produces: `sibling_md(pdf: Path) -> Path` (same folder, `<stem>.md`); `unread_scans(kb: KnowledgeBase) -> list[str]` (relative paths of indexed docs with status `needs_ocr` and no sibling `.md` on disk, sorted).
- Consumes: `kb.root`, `kb.index_path`, `connect_readonly` from `genea.index.db`.

- [ ] **Step 1: Write failing tests in `tests/test_scans.py`** (use the `kb` fixture; index it first the way `tests/test_indexer.py` does, with its helper for building an index):
  - `test_unread_scans_lists_sample_scan`: `unread_scans(kb) == ["docs/sken.pdf"]`.
  - `test_sibling_md_covers_scan`: write `docs/sken.md` under `kb.root`; `unread_scans(kb) == []`.
  - `test_sibling_md_uppercase_extension`: `sibling_md(Path("a/X.PDF")) == Path("a/X.md")`.
  - `test_status_hides_covered_scan`: after writing `docs/sken.md`, `index_summary(kb).problems` has no entry for `docs/sken.pdf`; before writing it, it does, and `counts` still lists the `needs_ocr` row either way.
- [ ] **Step 2: Run** `uv run pytest tests/test_scans.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `sibling_md` and `unread_scans` in `src/genea/ingest/scans.py` (query `files` where `kind = 'doc' AND status = 'needs_ocr'`; return `[]` when there is no readable index). In `index_summary`, drop problem rows whose status is `needs_ocr` and whose `sibling_md(kb.root / path)` exists.
- [ ] **Step 4: Run** `uv run pytest tests/test_scans.py tests/test_indexer.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: list scanned PDFs without an md and hide covered ones in status`

### Task 2: Page renderer

**Files:**
- Modify: `pyproject.toml` (extra `scan = ["pypdfium2>=4.30"]`; add `pypdfium2>=4.30` to the `dev` group), `src/genea/ingest/scans.py`
- Test: `tests/test_scans.py`

**Interfaces:**
- Produces: `render_pages(pdf: Path, out_dir: Path, dpi: int = 200) -> list[Path]` returning `out_dir/page-001.png`, `page-002.png`, ... in page order; creates `out_dir`; renders one page at a time (memory); raises `ScanError(str)` for a damaged or password-protected PDF.
- Consumes: nothing from Task 1.

- [ ] **Step 1: Write failing tests** with a two-page image-only PDF built by reportlab in `tmp_path` (blank pages, as in `tests/fixtures/make_kb.py:68`):
  - `test_render_pages_writes_numbered_pngs`: returns two paths named `page-001.png`, `page-002.png`; both open with PIL; width is about `612 * 200 / 72` (letter page, within 2 px).
  - `test_render_pages_replaces_old_page_only`: a pre-existing `page-001.png` is rewritten and an unrelated `notes.txt` in `out_dir` survives.
  - `test_render_pages_damaged_pdf_raises_scan_error`: a file with garbage bytes named `bad.pdf` raises `ScanError`.
- [ ] **Step 2: Run** `uv sync --extra scan` then `uv run pytest tests/test_scans.py -k render -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `render_pages` and `ScanError` in `src/genea/ingest/scans.py` with `pypdfium2.PdfDocument(path)`, `page.render(scale=dpi / 72).to_pil().save(...)`; wrap `pypdfium2.PdfiumError` into `ScanError`; import pypdfium2 inside the function and raise `ScanError("install the scan extra: uv sync --extra scan")` on `ImportError`.
- [ ] **Step 4: Run** `uv run pytest tests/test_scans.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: render PDF pages to PNG for reading`

### Task 3: CLI commands

**Files:**
- Create: `src/genea/cli/scancmds.py`
- Modify: `src/genea/cli/main.py` (register the module the way the other `*cmds` modules are imported there)
- Test: `tests/test_cli_scans.py` (follow the style of `tests/test_cli_index.py`)

**Interfaces:**
- Consumes: `unread_scans`, `render_pages`, `ScanError` from Task 1 and 2; `common.open_kb`, `common.console`.
- Produces: `genea scans` prints one relative path per line plus a final line `N scanned PDF(s) without an md` (exit 0; prints `No unread scanned PDFs.` when empty; exit 1 with the usual "not indexed" hint when there is no index). `genea render-pdf PDF OUT_DIR [--dpi 200]` prints one rendered path per line.

- [ ] **Step 1: Write failing CLI tests** (typer `CliRunner`):
  - `test_scans_lists_sample_scan`: output contains `docs/sken.pdf`.
  - `test_scans_empty_after_md`: after writing `docs/sken.md`, output contains `No unread scanned PDFs.`.
  - `test_render_pdf_writes_pages`: exit 0, `OUT_DIR/page-001.png` exists.
  - `test_render_pdf_missing_file_exit_2` and `test_render_pdf_not_a_pdf_exit_2`.
  - `test_render_pdf_damaged_exit_1_no_traceback`: output has no `Traceback`.
- [ ] **Step 2: Run** `uv run pytest tests/test_cli_scans.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** both commands in `src/genea/cli/scancmds.py`; `render-pdf` takes any PDF path (inside the KB or not), rejects a non-`.pdf` suffix or a missing file with exit 2, catches `ScanError` -> red message, exit 1.
- [ ] **Step 4: Run** `uv run pytest tests/test_cli_scans.py tests/test_cli_index.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: genea scans and render-pdf commands`

### Task 4: Skills and README

**Files:**
- Modify: `.claude/skills/extract-generic-document/SKILL.md`, `.claude/skills/extract-register-record/SKILL.md`, `.claude/skills/extract-census-record/SKILL.md` (one short "PDF input" paragraph each), `README.md:336`, and the `genea status` mention of OCR if present in `README.md`.

- [ ] **Step 1: Add a "PDF input" section** to `extract-generic-document` before "Steps": find work with `genea scans`; render with `genea render-pdf <pdf> <scratchpad folder>`; read every page image at full resolution (step 1 below applies); write one `<pdf stem>.md` next to the PDF with `### Page N` sections; never write into the KB except the `.md`; in "Several documents at once", one worker per PDF (or a few short PDFs). The register and census skills get a two-line pointer to that section.
- [ ] **Step 2: Update README**: replace "No OCR (scanned PDFs are listed as `needs_ocr` ...)" with the new flow (`genea scans`, `genea render-pdf`, the skill, and that status hides a PDF once its `.md` exists); document the `scan` extra next to the other extras.
- [ ] **Step 3: Verify** `grep -rn "No OCR" README.md` finds nothing; run `uv run pytest -q`. Expected: all pass.
- [ ] **Step 4: Commit** `docs: describe reading scanned PDFs through the extract skills`

---

## Self-review

- Spec coverage: list command (T3), render command (T2, T3), skill recipe (T4), indexer picks up `.md` (unchanged, covered by existing md indexing), status covered (T1), non-goals respected (no OCR, no schema/viewer change), review line kept (T4).
- Types match: `sibling_md(Path) -> Path`, `unread_scans(kb) -> list[str]`, `render_pages(pdf, out_dir, dpi) -> list[Path]`, `ScanError` used identically in T2 and T3.
- Left to the builder (per spec): exact typer option text and the helper used to build the index inside the tests.
