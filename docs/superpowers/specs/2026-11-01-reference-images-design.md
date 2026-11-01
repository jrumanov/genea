# Reference images in citation links: design

## Intent

Citation links open a local viewer (PR #27, #28). For images the viewer is thin: documents show only the
picture and its metadata, photos show no face positions, and TIFF is not shown at all. The user wants a
citation to an image to show what the researcher needs to check the claim: for a document, the page image
next to its transcript; for a photo, the picture with the searched person's face marked.

## Goals

1. JPG, PNG and TIFF open in the viewer. A TIFF is converted to JPG (the user's choice, not PNG).
2. A document image opens as two panes: the image, and the rendered MD file written for it (the user's choice,
   not the raw indexed text).
3. A photo opens with a box on the face of the person the citation is about. Face positions come from the
   face regions already saved in the file metadata (the user confirmed they exist).
4. Other tagged faces in the photo are shown as faint boxes with the name on hover (builder's call, not the
   user's).

## Non-goals

- No face detection or face recognition; only stored regions are read.
- No editing of regions, tags or originals. Originals are never modified.
- HEIC is out of scope (it keeps today's "open the original" note) unless the user asks.
- No OCR.

## Constraints

- Viewer stays local, read-only, and keeps the hardening in the existing viewer tests (path policy, `/raw/`).
- Citations written to disk keep stripping links (`links.STORED`); no new stored link forms.
- Build on the existing code: `viewer/photoview.py`, `viewer/links.py`, `viewer/server.py`, `exiftool.py`,
  `ingest/images.py`, the indexer, and the existing MD/text renderer.
- Windows machine, CPU only. Conversion happens on first open and is cached under `.genea`; it must not run
  during indexing.

## Decisions made with the user

- TIFF to JPG conversion, cached; original untouched.
- Transcript pane shows the MD file beside the image.
- Face region names: same spelling as the tree, but without diacritics, surname first, and sometimes a
  trailing birth year (e.g. `Rumanovsky Jakub 1985`).

## Behavior

### Documents
- A citation to an image that has a sibling MD file opens image + MD side by side. Without an MD file, the
  image and metadata open as today.
- Sides of one document (`_a1`, `_a2`) are switchable; the MD pane stays on the one MD file of the document.
  (Builder's call: confirm against how multi-side documents are written before building.)
- The sentence highlight from PR #28 applies in the MD pane when the link carries a span.

### Photos
- The link carries the tree person id the citation is about (new query parameter). Search results and
  `documents_about` pass it when the hit is about a known person; otherwise no box is drawn.
- Region-to-person matching: strip diacritics from the tree name, lowercase, compare as a set of words
  (order ignored); drop one trailing 4-digit year from the region name. If a year was present it must equal
  the tree person's birth year, otherwise no match. If more than one tree person still matches, mark every
  matching region (do not guess).
- The box is drawn from relative coordinates, so it is independent of displayed size and of conversion.
- No stored region for the person: the photo opens and says "no face position stored".

### TIFF
- Converted to JPG on first view, cached by content hash and page. EXIF orientation is applied during
  conversion so the stored region coordinates land correctly (builder's call: verify against a real rotated
  file).
- Multi-page TIFF: one JPG per page, chosen by the existing `page` parameter.

## Left to the builder

- Data model for regions (new table vs a column on `images`); the index must be rebuildable, so a schema
  bump is acceptable (it was done before, schema 2).
- Region standard to read (MWG `mwg-rs:Regions` is the likely one, with Microsoft `MP:RegionInfo` as a
  fallback): confirm by reading a real file with exiftool before choosing.
- JPG quality and a size cap for huge scans.
- Page layout, styling and narrow-window behavior of the two-pane view.

## Open questions for the builder to verify first

- Which region standard the user's files actually use, and whether coordinates are center-based or corner-based.
- Whether every register/letter image has an MD file next to it, and how the viewer finds it.
