# Reference Images Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Citation links open JPG, PNG and TIFF images in the viewer: scans beside their MD transcript, photos with a box on the searched person's stored face region.

**Architecture:** The viewer (`src/genea/viewer/`) gains a cached TIFF-to-JPG route, a two-pane page that pairs an image with its same-stem `.md` file (from either side), and a face-box overlay on photos. The indexer reads face regions from metadata into a new `image_regions` table (schema 3). A new query parameter `p=<person id>` on image links says whose face to mark; region names are matched to the tree person by diacritic-free, order-free word sets plus an optional birth year.

**Tech Stack:** Python 3, sqlite, stdlib `http.server`, ExifTool, Pillow (becomes a runtime dependency), pytest.

**Spec:** `docs/superpowers/specs/2026-11-01-reference-images-design.md`

## Global Constraints

- TIFF is converted to **JPG** (not PNG); the original file is never modified.
- Conversion happens on first view and is cached under the KB's `.genea` folder; never during indexing.
- The transcript pane shows the MD file written for the image: same folder, same stem, extension `.md` (the extract skills' convention).
- Region names: same spelling as the tree but no diacritics, surname first, optional trailing 4-digit birth year (`Rumanovsky Jakub 1985`).
- Viewer stays read-only and keeps its hardening (`lookup()` path checks, `is_excluded` tags, Host and fetch checks). Nothing new may expose a file that `lookup()` would refuse.
- Links written to disk keep stripping (`links.STORED`); no new stored link forms.
- HEIC stays unsupported (keeps today's "open the original" note). No face detection, no region editing.
- Windows, CPU only.

## Review Focus

1. Image with an excluded tag (`viewer/policy.py`) has a sibling `.md`: the MD page must not show or link its image, and the image page must still 404.
2. A region with a missing name, non-numeric or out-of-range area: skipped; indexing the photo still succeeds.
3. A region name that matches two tree people, or whose year differs from the birth year: both marked / none marked, never a silent guess.
4. A corrupt, zero-byte or very large TIFF, or `?page=` out of range or non-numeric: a notice page, never a 500.
5. `p=` naming an unknown person or a malformed id: the photo opens with no box and no crash.

---

### Task 1: Serve TIFF as cached JPG

**Files:**
- Create: `src/genea/viewer/convert.py`
- Modify: `src/genea/viewer/server.py` (the `raw` branch, ~line 163), `src/genea/viewer/photoview.py` (`NOT_SHOWN`, picture markup), `pyproject.toml` (move `pillow>=12.0` from the dev group into `dependencies`)
- Test: `tests/test_viewer_images.py` (create; copy the `get`, `start` and `add_photo` helpers from `tests/test_viewer_pdf_photo.py`)

**Interfaces:**
- Produces: `convert.tiff_page_count(path: Path) -> int`; `convert.jpeg_for(kb: KnowledgeBase, rel: str, page: int = 0) -> bytes` (raises `convert.ConvertError`); `/raw/<tif>?page=N` returns `image/jpeg`.

- [ ] **Step 1: Write the failing tests** in `tests/test_viewer_images.py`: `test_tiff_raw_is_served_as_jpeg` (a 2-page TIFF made with Pillow; `GET /raw/photos/a.tif` → 200, `Content-Type: image/jpeg`, body starts with `b"\xff\xd8"`), `test_tiff_page_param_picks_page` (page 1 differs from page 0 in size), `test_tiff_conversion_is_cached` (second request does not call `Image.open` again: monkeypatch and count), `test_tiff_applies_exif_orientation` (orientation 6 swaps width and height), `test_tiff_bad_page_and_corrupt_file` (`?page=9`, `?page=x` and a file of `b"x"` give a 404/notice status < 500, never 500), `test_tiff_photo_page_shows_img_tag` (`/v/photos/a.tif` contains `<img src="/raw/photos/a.tif`).
- [ ] **Step 2: Run** `uv run pytest tests/test_viewer_images.py -v` → FAIL.
- [ ] **Step 3: Implement `convert.py`.** Open with Pillow, `ImageOps.exif_transpose`, convert to RGB, cap the longer side at 4000 px, JPEG quality 88. Cache file `<kb.index_path.parent>/viewer-cache/<sha256 of file bytes first 16 hex>-<page>.jpg`, written atomically (temp + replace). Catch `Image.DecompressionBombError`, `OSError`, `EOFError` and a page beyond `n_frames` → `ConvertError`.
- [ ] **Step 4: Wire the server.** In the `raw` branch, when the suffix is `.tif`/`.tiff`, call `convert.jpeg_for` with `int_param`-style parsing of `?page` (digits only, else page 0 → 404 notice for out of range) and send `image/jpeg`; `ConvertError` → 404 with `notice_page`. `photoview` drops `.tif`/`.tiff` from `NOT_SHOWN` and uses `/raw/...?page=N` (N from `pages.int_param(params, "page")`, default 0; `render_photo` gets a `params` argument).
- [ ] **Step 5: Run** `uv run pytest tests/test_viewer_images.py tests/test_viewer_pdf_photo.py tests/test_viewer_hardening.py -v` → PASS.
- [ ] **Step 6: Commit** `git add -A && git commit -m "Show TIFF images in the viewer as cached JPG"`.

### Task 2: Read face regions into the index

**Files:**
- Modify: `src/genea/ingest/images.py` (`FaceRegion`, `ImageRecord.regions`, `map_fields`, `_record`), `src/genea/exiftool.py` (only if the real files need `-struct`; see Step 1), `src/genea/index/db.py` (schema 3, `image_regions`), `src/genea/index/indexer.py` (~line 170, insert regions)
- Test: `tests/test_images.py`, `tests/test_indexer.py`

**Interfaces:**
- Produces: `@dataclass FaceRegion(name: str, x: float, y: float, w: float, h: float)` (top-left corner and size, all 0..1 of the displayed image); `ImageRecord.regions: list[FaceRegion]`; table `image_regions(id INTEGER PRIMARY KEY, image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE, name TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL)` with an index on `image_id`; `SCHEMA_VERSION = "3"`.

- [ ] **Step 1: Look at a real file first.** Run `exiftool -json -G1 -n -struct "<one tagged photo from D:\Foto DB>"` and without `-struct`; note which standard carries the regions (MWG `XMP-mwg-rs`, Microsoft `XMP-MP`), whether tag names are flat or nested, and that MWG `Area` x/y are the **center**. Save a trimmed copy of the real output as `tests/fixtures/regions_sample.json` (names replaced with invented ones). Choose the extraction that works with `read_metadata`'s current flat `-G1 -n` output; add a keyword to `read_metadata` only if the flat form cannot express the regions.
- [ ] **Step 2: Write the failing tests** in `tests/test_images.py`: `test_regions_parsed_from_mwg_metadata` (fixture → `FaceRegion("Novak Jan 1932", x=cx-w/2, y=cy-h/2, ...)` with the exact numbers), `test_regions_skip_bad_entries` (empty name, missing area, area value > 1 or < 0, non-number: skipped, others kept), `test_sidecar_regions_replace_embedded` (sidecar provides regions → embedded ones dropped, as other fields do). In `tests/test_indexer.py`: `test_regions_are_indexed` (rows in `image_regions` with the right `image_id`, count and values) and update any test asserting schema `"2"`.
- [ ] **Step 3: Run** `uv run pytest tests/test_images.py tests/test_indexer.py -v` → FAIL.
- [ ] **Step 4: Implement** `parse_regions(md: dict) -> list[FaceRegion]` in `ingest/images.py` and call it from `map_fields` (key `regions`, provided when the tags are present, empty list clearing like the other list fields); fall back to the Microsoft standard only if Step 1 showed some files use it. Clamp nothing: out-of-range values are skipped. Indexer inserts one row per region after the `images` row. Bump `SCHEMA_VERSION`.
- [ ] **Step 5: Run** the full suite `uv run pytest -q -n auto` → PASS (index-version tests included).
- [ ] **Step 6: Commit** `git commit -am "Index face regions from image metadata (schema 3)"`.

### Task 3: Match region names to a tree person

**Files:**
- Create: `src/genea/viewer/regions.py`
- Test: `tests/test_viewer_regions.py` (create)

**Interfaces:**
- Consumes: `genea.text.normalize`, tables `persons`, `events`, `image_regions`; `genea.tools.people.first_event`/`event_rows`/`BIRTH_TYPES`.
- Produces: `region_matches(region_name: str, forms: list[str], birth_year: int | None) -> bool`; `person_forms(conn, ged: str, xref: str) -> tuple[list[str], int | None]` (name forms from `names_norm`/`name_variants`, birth year from the first birth-type event's `date_from`); `parse_person_param(conn, raw: str) -> tuple[str, str] | None` (accepts the `tree.ged#@I12@` form, returns None unless the person row exists); `image_regions_for(conn, rel: str) -> list[sqlite3.Row]`.

- [ ] **Step 1: Write the failing tests:** `test_diacritics_and_order_ignored` (`"Rumanovsky Jakub"` matches forms of `"Jakub Rumanovský"`), `test_trailing_year_must_equal_birth_year` (`"Rumanovsky Jakub 1985"` matches birth 1985, not 1986; with `birth_year=None` it matches), `test_year_only_stripped_when_four_digits_at_end`, `test_different_words_do_not_match` (`"Rumanovsky Jan"` vs `Jakub`), `test_two_people_same_name_both_match_without_year`, `test_unknown_or_malformed_person_param_returns_none` (`"x"`, `"tree.ged#@I999@"`, `""`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** with word-set equality of `normalize(name)` words (year token removed) against each form's words. Birth year from `date_from[:4]` when it is digits.
- [ ] **Step 4: Run** `uv run pytest tests/test_viewer_regions.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "Match face-region names to tree people"`.

### Task 4: Carry the person in image links

**Files:**
- Modify: `src/genea/viewer/links.py` (`build_link`), `src/genea/tools/models.py` (`SourceRef`), `src/genea/tools/search.py` (`find_images`, ~line 247)
- Test: `tests/test_links.py`, `tests/test_search.py`

**Interfaces:**
- Produces: `build_link(..., person: str | None = None)` adds `p=<person>` to the query; `SourceRef.person: str | None = None` passed through to `build_link`. `find_images` sets `person` on each image's `SourceRef` when called with a person id.

- [ ] **Step 1: Write the failing tests:** `test_build_link_adds_person` (`?p=tree.ged%23%40I12%40`, check the exact encoded form `urlencode` produces), `test_build_link_without_person_unchanged`, `test_find_images_by_person_id_links_with_person` (link ends with the encoded `p=`; a find_images by plain name has no `p=`), `test_sourceref_person_not_serialized_when_links_stripped`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the keyword and the field; `SourceRef.person` is `exclude=True` so it never appears in output.
- [ ] **Step 4: Run** `uv run pytest tests/test_links.py tests/test_search.py tests/test_mcp_server.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "Image links carry the person they are about"`.

### Task 5: Face boxes on photos

**Files:**
- Modify: `src/genea/viewer/photoview.py`, `src/genea/viewer/pages.py` (`STYLE` for `.frame`, `.box`, `.box.faint`)
- Test: `tests/test_viewer_images.py`

**Interfaces:**
- Consumes: Task 3 (`parse_person_param`, `person_forms`, `region_matches`, `image_regions_for`), Task 1 (`render_photo(kb, conn, rel, params)`).
- Produces: photo page markup `<div class="frame"><img …><div class="box" style="left:X%;top:Y%;width:W%;height:H%" title="NAME"></div>…</div>`; matching regions use class `box`, others `box faint`; a note `No face position stored for this person.` when `p` names a known person and nothing matches.

- [ ] **Step 1: Write the failing tests:** `test_matching_region_is_boxed` (index a photo with two regions, one matching `p`; exactly one `class="box"` with the exact percentages, one `box faint` with the name in `title`), `test_no_match_shows_note`, `test_unknown_p_shows_no_box_and_no_note_no_crash`, `test_two_matching_people_marks_both`, `test_regions_only_on_first_tiff_page` (`?page=1` draws no boxes), `test_names_html_escaped` (a region name containing `<script>`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the overlay (percent units, one `.frame {position:relative; display:inline-block}`; `img` stays `max-width:100%`).
- [ ] **Step 4: Run** `uv run pytest tests/test_viewer_images.py tests/test_viewer_pdf_photo.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "Mark the searched person's face on photos"`.

### Task 6: Image beside transcript

**Files:**
- Create: `src/genea/viewer/sidebyside.py`
- Modify: `src/genea/viewer/textview.py` (extract the body of `render_text` into `text_body(kb, conn, rel, params) -> str`), `src/genea/viewer/pages.py` (`shell(title, body, wide=False)`, `two_pane(left: str, right: str) -> str`, `render` passes through), `src/genea/viewer/photoview.py`
- Test: `tests/test_viewer_images.py`

**Interfaces:**
- Consumes: `server.lookup` (reuse it so excluded and unreadable files are refused), `ingest.images.IMAGE_EXTS`.
- Produces: `sidebyside.sibling_image(kb, conn, rel_md: str) -> str | None` (same folder and stem, extension in `IMAGE_EXTS`, passes `lookup`); `sidebyside.sibling_md(kb, conn, rel_image: str) -> str | None` (same stem + `.md`, passes `lookup`); `sidebyside.sides(conn, rel: str) -> list[str]` (indexed files sharing the stem with a trailing `_<letter><digits>` removed, e.g. `_a1`/`_a2`, sorted, excluding `rel` itself, each filtered through `lookup`).

- [ ] **Step 1: Write the failing tests:** `test_md_page_shows_image_pane` (`scan_a1.jpg` + `scan_a1.md`; `/v/…/scan_a1.md` has an `<img src="/raw/…scan_a1.jpg"` and the MD text, in a two-pane container), `test_image_page_shows_md_pane`, `test_md_without_image_unchanged` (page identical to today's, no pane), `test_excluded_image_not_shown_from_md` (image tagged `resident permit`: MD page has no `<img` and no `/raw/` link to it; `/v/` of the image is 404), `test_sides_links_to_other_side` (`_a1` page links `/v/…_a2.md`'s sibling pages, and not to itself), `test_chunk_highlight_still_applies_in_md_pane` (`?chunk=0&o=..&n=..` yields the `<mark id=hit>` inside the right pane), `test_tiff_scan_with_md` (TIFF + MD: image pane uses `/raw/….tif?page=0`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `two_pane` is a CSS grid (`1fr 1fr`, stacks under 50rem); `shell(wide=True)` lifts the body's `max-width`. The MD pane reuses `text_body` (plain wrapped text with the existing highlighting; no Markdown renderer is added). A `.md` page whose image is missing, or an image page whose `.md` is missing, renders exactly as before.
- [ ] **Step 4: Run** the whole viewer set `uv run pytest tests/test_viewer_*.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "Show scans beside their transcript"`.

### Task 7: Docs and live check

**Files:**
- Modify: `README.md` (viewer section: TIFF as JPG, scan + transcript, face boxes, region name matching, reindex needed for schema 3)

- [ ] **Step 1: Update the README** with the behavior above in the viewer section's existing style, and note that the index must be rebuilt once.
- [ ] **Step 2: Run** `uv run pytest -q -n auto` → all pass; `uv run ruff check src tests` if the project uses it (check `pyproject.toml`).
- [ ] **Step 3: Live check** (user's machine, needs the rebuilt index of `D:\Foto DB`): open a citation to a register MD, a TIFF scan, and a photo found with `find_images` by person id; confirm side-by-side, JPG shown, and the box lands on the right face. Report what you saw; fix only what the check shows.
- [ ] **Step 4: Commit** `git commit -am "Document reference images in the viewer"`.

---

## Self-review notes

- Spec coverage: TIFF→JPG (T1), regions read (T2), name matching incl. diacritics/order/year (T3), person in link (T4), face box + faint others + no-position note (T5), image beside MD both ways, sides, highlight (T6), multi-page TIFF (T1, T5), originals untouched and cache under `.genea` (T1), reindex/schema (T2, T7). HEIC and detection stay out.
- One deviation to flag to the user: spec said "a citation to an image opens image + MD"; code check showed citations to scans point at the **MD** file, so T6 serves both directions.
- Person is only known to the link when the caller passed a person id; a `find_images` by plain name text gets no box (stated in T4's test).
