---
name: extract-generic-document
description: Use when the user asks to extract, transcribe or write an MD file from a document image or PDF that is not a register (matrika) page or census sheet, such as a certificate, court or office decision, letter, school record, permit, card or newspaper clipping.
---

# Extract a whole document into an MD file

Transcribe the document as written, then add a structured section and an English summary. Register
pages and census sheets have their own skills (`extract-register-record`, `extract-census-record`).

## Several documents at once

Group parts of one document (`_a1`/`_a2`, `a`/`b`, `_1`/`_2`) and skip images that already have an
`.md`. Then start background workers (Agent tool, `run_in_background: true`, all in one message), a few
documents each, none twice. Each worker prompt: invoke this skill, give the exact file names, repeat
"never overwrite an `.md`, always the review line, no git commands", and ask for a short report per
document (path, language, doubtful values). Report to the user only what workers reported.

## Steps

1. **Read at full resolution.** The Read tool downscales large scans. For TIFFs, small print or
   handwriting, crop or convert with PIL (`Image.MAX_IMAGE_PIXELS = None`) into the scratchpad and read
   the pieces. Note the language(s) of the text.
2. **Write the MD** next to the image, same name with `.md` (parts of one document: one file named
   after the first part without its suffix, pages in order under `### Page <suffix>`). Never overwrite
   an existing `.md`: show the difference instead.

## MD content, in this order

1. **Header line**: document type, issuer, place, date. Directly under it, with a blank line before and
   after, this exact line:
   `Review status: NOT REVIEWED (machine transcription, check every value against the image)`
2. **`## Transcript`**: the full text as written, in the original language, line breaks and spelling
   kept, nothing corrected or modernized. Doubt: `[?]` after the word; `[illegible]`; `[...]` for damage.
   Non-text elements in italic brackets: `*[stamp: ...]*`, `*[signature]*`, `*[handwritten: ...]*`,
   `*[margin: ...]*`. Include letterhead, reference numbers, stamps and handwriting.
3. **`## Structured data`**: in the original language of the document (field names too; for a mixed
   document, the main language). One line per field, only what the document states, original form
   kept. Use the fields that apply: type, issuing authority, place, date, reference number, subject,
   people with role (named person, parents, spouse, witness, signer, official), birth / marriage /
   death data, addresses, occupation, amounts, legal basis, decision, stamps and seals. Skip a field the
   document does not give; never infer from outside knowledge.
4. **`## Summary`**: two to four sentences in English: what the document is, who it concerns, what it
   decides or certifies, and its date. Always English, whatever language the user writes in.
5. **`## Translation`**: only if the document language is not Slovak, Czech, German or English
   (Hungarian, Latin, Polish, Yiddish, Hebrew and so on). A complete English translation of the
   transcript, same order; keep names, places and `[?]` marks as in the transcript. Omit the section
   otherwise.

Tables (grade lists, columns): pad every cell so the pipes line up in the raw text, with a
`| --- | --- |` separator row padded to the column width (`| Polrok    | I.  | II. |`).

Style follows the existing MD files in `rodinna historia/dokumenty_sorted`: plain Markdown, no
commentary about the source beyond the bracketed notes.

## Review flag

Every file is a machine transcription and carries the review line above. A human replaces it with
`Review status: reviewed by <name>, <YYYY-MM-DD>`. Never write "reviewed" yourself and never change a
flag a human set; if asked to edit a reviewed file, keep the flag and tell the user which values you
changed. Find waiting files: `grep -rl "Review status: NOT REVIEWED" <folder>`.

## Before saying done

- Transcript is complete and matches the image; doubtful readings are marked, not smoothed over.
- Structured data repeats only transcript facts; the summary adds none.
- Review line present; Translation section present only for languages outside sk/cs/de/en.
- Tell the user: path written, document language, doubtful values, anything inconsistent inside the
  document (for example two different years for the same event).
