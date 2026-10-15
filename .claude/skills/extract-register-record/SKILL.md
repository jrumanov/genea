---
name: extract-register-record
description: Use when the user asks to extract, transcribe or write an MD file from a birth, baptism, marriage or death register (matrika) page image or PDF, named like <YYYY_MM_DD>-<surname>_<given>-<book type in Slovak>.<ext>.
---

# Extract one person's record from a register page

A register page (church or civil book) holds many entries. The MD file is about ONE person (or one
couple), and the file name says who. Everything else on the page is out of scope.

## Several records at once: use parallel workers

When asked for a folder or more than one record, do not read them one after another in the main
session. List the images first, drop the ones that already have an `.md` and group `_a1`/`_b2` style
parts of one document together, then start background workers (Agent tool, `run_in_background: true`),
all in one message so they run in parallel. Give each worker a few records, never the same record twice,
and a prompt that:

- tells it to invoke this skill and follow it for its files, with the folder and the exact file names;
- repeats the rules that matter: never overwrite an `.md`, always the `Review status: NOT REVIEWED`
  line, no git commands, only the new `.md` files are written;
- tells it to crop the entry at full resolution (Python PIL, `Image.MAX_IMAGE_PIXELS = None`, crops in
  the scratchpad), because the Read tool downscales large scans and the handwriting becomes unreliable;
- asks for a short report per record: path written, which entry was taken, doubtful values, and any
  file name date that differs from the entry.

A single record can be done inline. When the workers finish, give the user one combined report from
their reports (do not claim anything a worker did not report) and name any record that failed or was
skipped.

## 1. Read the target from the file name

`<YYYY_MM_DD>-<Surname>_<Given>[-<Surname2>_<Given2>]-<book>[_<suffix>].<ext>`

| Part | Meaning |
|---|---|
| `YYYY_MM_DD` | Date of the event (birth/baptism, marriage or death). Parts may be missing (`1941-`) or use `-` (`1844_07-14`): take what is there. |
| `Surname_Given` | The person to find. For a marriage there are two: groom first, bride second. Spelling is the user's normalized one; the book may spell it differently (Bozsik / Bóžik / Bózsik, Janos / János / Joannes), so match by sound, date and parents, not by exact letters. |
| `<book>` | `rodny_zaznam`, `rodny_krstny_zaznam` (birth / baptism), `svadobny_zaznam`, `sobasny_zaznam` (marriage), `umrtny_zaznam` (death). Others (`skolsky_zaznam`, `supis_zidov_odpis`) are not register entries: ask the user. |
| `_a1`, `_b2`, `_b` | Page or part of the same document. Output name below. |

Say the parsed target (person, event date, book type) to the user in one line before reading the image.

## 2. Find only that entry

Read the image (Read tool, or the PDF with `pages`). Locate the one entry whose date and name match the
target; for a baptism the entry date can be a day or two after the birth. Search ONLY for that name:

- Do not transcribe neighbouring entries, even if they are relatives or on the same line.
- Names of other people appear only when they are part of the target's own entry (parents, godparents,
  witnesses, spouse, informant, priest).
- If no entry matches, or several could (twins, same name twice that year), stop and tell the user
  what you saw. Do not guess and do not fill the file with the closest candidate.
- **Name matches, date differs.** If exactly one entry on the page fits the name (and the surname
  and parents are plausible) but its date is not the one in the file name (the file name date may be
  wrong, e.g. copied from a neighbouring entry), still take that entry. Do not stop and do not
  change the file name. Add a line `*[Note: file name date YYYY_MM_DD; entry gives <dates as
  written>]*` under the header, and say it first in your final report, with any other file in the
  folder that supports one of the dates. If a second entry fits the name too, it is the "several
  could" case: stop.

## 3. Write the MD file

Save next to the image, same name, extension `.md` (for `_a1`/`_b2` style parts, one `.md` per image
that holds the entry; the file name equals the image's). Never overwrite an existing `.md`: show the
user the difference instead.

Content, in this order:

1. **Header line** with the book, volume/page/entry number if shown, parish/place, and year, followed
   by the review flag line (see below).
2. **Entry as written**: the target's entry transcribed in its original language (Latin, Hungarian,
   German, Slovak), line by line, keeping original spelling. Mark doubt: `[?]` after an uncertain word,
   `[illegible]` for unreadable text, `[...]` for damage. Marginal notes belong to the entry only if
   they point at it; keep them as `*[margin: ...]*`.
3. **Extracted facts** as a short list, only what the entry states, one line each, with the original
   form kept: name, sex, date and place of birth / baptism / marriage / death, age, residence,
   religion, legitimacy, father, mother (with maiden name), godparents, witnesses, priest or
   registrar, cause of death, burial. Skip a field the entry does not give; never infer from
   outside knowledge.
4. **English reading**: a faithful translation of the entry in one or two sentences, always in English
   (the entry text and field values stay in the original language), whatever language the user writes
   to you in.

Style follows the existing MD files in `rodinna historia/dokumenty_sorted`: plain Markdown, bracketed
italic notes `*[...]*` for non-text things (stamps, signatures, margin notes), no commentary about
the source beyond that.

**Review flag (mandatory).** Every file you write is a machine transcription and must carry this exact
line directly under the header line (at the very top when a file has no header), with a blank line
before and after:

```
Review status: NOT REVIEWED (machine transcription, check every value against the image)
```

A human then reads the file against the image, corrects it and replaces the line with
`Review status: reviewed by <name>, <YYYY-MM-DD>`. Never write "reviewed" yourself and never change a
flag a human has set; when asked to edit a file that already says "reviewed", keep the flag and tell the
user which values you changed so they can re-check them. To find the files still waiting:
`grep -rl "Review status: NOT REVIEWED" <folder>`.

## 4. Check before saying done

- The file names only the target (and the people inside their own entry).
- The `Review status: NOT REVIEWED` line is present under the header.
- Every date and name matches the image (a file name date that differs is noted, see above); doubtful readings are marked, not smoothed over.
- Tell the user: which entry you took (line or number on the page), what was doubtful, and the path
  written. If the page held other entries you skipped, say so in one line without listing them.
