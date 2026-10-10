---
name: extract-census-record
description: Use when the user asks to extract, transcribe or write an MD file from a census sheet (scitaci harok, supis obyvatelstva, Volkszahlung) image whose metadata carries the census tag, usually named like <YYYY_MM_DD>-<place>_<house no>[-scitaci_harok]_a1.<ext> with a matching _a2 back side.
---

# Extract the tagged people from a census sheet

A census sheet (house sheet) lists everyone living in one house. The MD file covers ONLY the people
tagged on the image, plus the house header and the census date. Everyone else on the sheet is out of scope.
The writing is usually Slovak handwriting (older sheets can be Czech, Hungarian or German).

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

1. **Header**: `# Census <year>, <city/village>, <street> <house no.>`, then a line with the census
   day from the back (as written) and the owner of the house.
2. **Header block as written**: top-left and top-right text, line by line, original spelling.
3. **One section per tagged person** (`## <name as written on the sheet>`): the row as written, then
   the fields as a list, one per line, heading from the form first and the value as written:
   `- Relation to head of household: ...`. Skip a field the sheet does not give.
4. **Reading**: one or two sentences per person in the language the user writes to you in.

Use bracketed italic notes `*[...]*` for non-text things (stamps, signatures, margin notes), the same
as the other MD files in `rodinna historia/dokumenty_sorted`.

## 6. Check before saying done

- The file names only the tagged people, the owner of the house and, inside their rows, relation
  targets.
- Every name, number and date matches the image; doubtful readings are marked, not smoothed over.
- Tell the user: which tagged people were found on the front, back or not at all, what was blacked
  out or doubtful, and the path written. If the sheet held other people you skipped, say so in one
  line without listing them.
