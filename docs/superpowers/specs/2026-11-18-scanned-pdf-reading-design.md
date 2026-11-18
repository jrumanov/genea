# Reading PDFs without a text layer (design)

## Intent
Scanned PDFs (each page a photo of a paper page, often handwritten) are marked `needs_ocr` and are
invisible to search. The user wants them readable. Chosen route: Claude vision through Claude Code
subagents, the same way register, letter and census images become reviewed `.md` files today (no API
spend, handles handwriting).

## Goals
- A command lists `needs_ocr` PDFs that still have no `.md` beside them.
- A command renders a PDF's pages to PNG files in a given folder so a subagent can read them.
- `extract-generic-document` (and the register/census skills) say how to handle a multi-page PDF: render
  pages, read at full resolution, write one `.md` next to the PDF (`<pdf stem>.md`, `### Page N` sections).
- The existing indexer picks up the `.md`; its chunks keep the `Review status: NOT REVIEWED` line.
- `genea status` shows a `needs_ocr` PDF that has a sibling `.md` as covered, not as a problem.

## Non-goals
- No local OCR (Tesseract etc.) and no automatic reading during `genea index`.
- No Claude API calls from genea code.
- No changes to the PDF viewer or to the schema.

## Anti-goals
- A machine reading must never look like verified text: the review line stays, and the skills never
  write "reviewed".
- Never overwrite an existing `.md`.

## Approaches considered
1. Claude vision only (chosen): fits the current workflow, reads handwriting; manual and slower.
2. Local OCR in `genea index`: automatic but poor on handwriting and Slovak/Czech diacritics.
3. Hybrid (OCR first pass, Claude upgrade): most work, new dependency; can be added later.

## Calls I made (not discussed)
- Renderer: `pypdfium2` (a pip wheel, no system install; pypdf cannot render pages). New optional
  extra `scan`, like the existing `transcribe` extra.
- Render at about 200 DPI, one `page-001.png` per page, written only to a folder the caller names
  (the scratchpad), never into the KB.
- The `.md` citation is `[folder/x.md]` rather than a PDF page; the `### Page N` headings carry the page.
- Command names are left to the builder, in the style of the existing `genea` subcommands.

## Left to the builder
Exact CLI names and flags, where the sibling-`.md` check lives (indexer vs status), tests with a
generated image-only PDF fixture, README update (remove "No OCR" wording, describe the flow).
