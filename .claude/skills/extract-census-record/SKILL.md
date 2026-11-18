---
name: extract-census-record
description: Use when the user asks to extract, transcribe or write an MD file from a census sheet (scitaci harok, supis obyvatelstva, Volkszahlung) image whose metadata carries the census tag, usually named like <YYYY_MM_DD>-<place>_<house no>[-scitaci_harok]_a1.<ext> with a matching _a2 back side.
---

# Extract the tagged people from a census sheet

PDF without a text layer: render its pages first with `genea render-pdf` (see "PDF input" in `extract-generic-document`) and read the page images.

A census sheet (house sheet) lists everyone living in one house. The MD file covers ONLY the people
tagged on the image, plus the house header and the census date. Everyone else on the sheet is out of scope.
The writing is usually Slovak handwriting (older sheets can be Czech, Hungarian or German).

## Several sheets at once: use parallel workers

When asked for a folder or more than one sheet, do not read them one after another in the main session
(each sheet costs several large images). List the sheets first: group front and back files into
sheets, drop the ones that already have an `.md`, then start background workers (Agent tool,
`run_in_background: true`), all in one message so they run in parallel. Give each worker one or two
sheets, never the same sheet twice, and a prompt that:

- tells it to invoke this skill and follow it for its sheets, with the folder and the exact front/back
  file names (and the MD name when it differs, e.g. a `-a1` suffix or a name without `-scitaci_harok`);
- repeats the rules that matter: never overwrite an `.md`, always the `Review status: NOT REVIEWED`
  line, no git commands, only the new `.md` files are written;
- tells it to crop the tagged rows, the header and the back signature block at full resolution
  (Python PIL, `Image.MAX_IMAGE_PIXELS = None`, crops in the scratchpad), because the Read tool
  downscales the large scans and the handwriting becomes unreliable;
- asks for a short report per sheet: path written, tagged people found on front, back or not at all,
  tag-to-row matches, doubtful or blacked-out values.

A single sheet, or one or two quick ones, can be done inline. When the workers finish, give the user one
combined report from their reports (do not claim anything a worker did not report) and name any sheet
that failed or was skipped.

## 1. Check the tags

```
exiftool -s3 -XMP-digiKam:TagsList <image>
```

- The image is a census sheet only if the list contains a census tag: `_documents/census` (main
  tree) or `_deni/_dokumenty/_census` (Deni's tree). If not, stop and tell the user (do not guess
  from the file name).
- People to extract = every tag whose last segment is a name: `_indirect_family/Kvasnicka Stefan`,
  a bare `Kvasnicka Jozef`, `_deni/Cifrulak Maria` all count (take the part after the last `/`).
  Segments starting with `_` are categories, not people; skip the tags `_deni`, `_documents/...`,
  `_deni/_dokumenty/...` and `_deni/_miesta/<place>`, and any other non-person tag (say so).
- A place tag (`_deni/_miesta/Sliepkovce`) is not a person, but it confirms the village for the header.
- Tag spelling is the user's normalized one without diacritics; the sheet may spell it differently
  (Kvasnicka / Kvasnička, Stefan / Štefan), so match by sound, age and position in the household.
- **Married women**: the sheet writes a wife under her married name (Cifrulaková), while her tag can
  carry the maiden name (Salitros Zuzana). When a tagged woman is not found by her own surname, look
  for a row whose relation to the head is `manželka` (or whose family status is `vydatá`) with the
  same given name, in a household where the husband's surname is also tagged (Cifrulak Michal). Take
  that row. If several rows fit (two wives, two women of that given name), stop and ask. Never
  write the maiden name into the file unless the sheet itself gives it; say in the report which
  tag you matched to which row.
- Front and back images carry the same tags.

Say the parsed target (file, census date from the name, list of tagged people) to the user in one line
before reading the image.

## 2. Front page (`_a1`, or `_a` when the pair is `_a` / `_b`)

Read the image (Read tool). From the top of the sheet take:

- **Top left**: street and/or city/village (municipality, district), house number if shown there.
- **Top right**: street/city as printed there, house number, and the **owner of the house**.
- **Rows**: find the row of each tagged person and transcribe the whole row, every column, with the
  column heading it belongs to (name, relation to head of household, sex, birth date and place,
  religion, nationality, marital status, occupation, literacy, residence status, etc., as the form
  has them).

Do not transcribe rows of untagged people, even relatives in the same household. Their names appear
only where a column of a tagged person points at them (for example "wife of ...", "son of ...").

## 3. Back page (`_a2`, or `_b`)

Read the back when:

- a tagged person was not found on the front (the household can continue overleaf), or
- the census date is needed (it is written on the back, in the enumerator / certification block).

From the back take the **day the census was done** as written, the rows of any tagged person still
missing, and nothing else. If the sheet has more parts (`_c`, `_d`), open them only for tagged people
still missing.

Say which tagged people you could not find on any side; never fill their rows from other people.

## 4. Blacked-out and unreadable data

If part of the sheet is blacked out or covered, read only what is available. Write `[blacked out]`
for covered fields, `[illegible]` for unreadable text, `[?]` after an uncertain word or number,
`[...]` for damage. Do not infer a hidden value from the other rows, from the tree or from outside
knowledge.

## 5. Write the MD file

Save next to the image, named like the front image but WITHOUT the page suffix (`_a1`, `_a`) and with
the extension `.md`: `1930_12_02-Sliepkovce_73-scitaci_harok_a1.jpg` gives
`1930_12_02-Sliepkovce_73-scitaci_harok.md`. One file covers the front and back together. Never
overwrite an existing `.md`: show the user the difference instead.

Content, in this order:

1. **Header**: `# Census <year>, <city/village>, <street> <house no.>`, then the review flag line
   (see below), then a line with the census day from the back (as written) and the owner of the house.
2. **Header block as written**: top-left and top-right text, line by line, original spelling.
3. **One section per tagged person** (`## <name as written on the sheet>`): the row as written, then
   the fields as a list, one per line, heading from the form first and the value as written:
   `- Relation to head of household: ...`. Skip a field the sheet does not give.
4. **Reading**: one or two sentences per person, always in English (the sheet text and field values stay
   in the original language; only the reading is English), whatever language the user writes to you in.

Use bracketed italic notes `*[...]*` for non-text things (stamps, signatures, margin notes), the same
as the other MD files in `rodinna historia/dokumenty_sorted`.

**Review flag (mandatory).** Every file you write is a machine transcription and must carry this exact
line directly under the `#` title (at the very top when a file has no title), with a blank line before
and after:

```
Review status: NOT REVIEWED (machine transcription, check every value against the image)
```

A human then reads the file against the image, corrects it and replaces the line with
`Review status: reviewed by <name>, <YYYY-MM-DD>`. Never write "reviewed" yourself and never change a
flag a human has set; when asked to edit a file that already says "reviewed", keep the flag and tell the
user which values you changed so they can re-check them. To find the files still waiting:
`grep -rl "Review status: NOT REVIEWED" <folder>`.

## 6. Check before saying done

- The file names only the tagged people, the owner of the house and, inside their rows, relation
  targets.
- Every name, number and date matches the image; doubtful readings are marked, not smoothed over.
- The `Review status: NOT REVIEWED` line is present under the title.
- Tell the user: which tagged people were found on the front, back or not at all, what was blacked
  out or doubtful, and the path written. If the sheet held other people you skipped, say so in one
  line without listing them.
