---
name: sync-and-extract
description: Use when the user asks to sync new files of the genea knowledge base and extract them, or says new documents were added, to index new images, tag them and write their MD files.
---

# Sync new files, tag them, extract them

New images have no tags. The user gives the tags and people; never guess them from the image. One
review table is the only stop. The knowledge base is `D:\Foto DB` (index `D:\Foto DB\.genea`); never
use the `rodinna historia` subfolder as `--kb`. Run Python as `.venv\Scripts\python.exe` (the default
Python has no PIL or genea).

## 1. Sync and find the new files

1. Read the paths already indexed (read-only): `select path from files` in
   `D:\Foto DB\.genea\index.db`, saved in the scratchpad.
2. Run `.venv\Scripts\genea.exe --kb "D:\Foto DB" index --no-summaries` (never `--rebuild`). If it
   reports the index busy, in use or locked, stop and tell the user.
3. New files = indexed image paths that were not in the list from step 1 and have no `.md`. Group
   parts of one document (`_a1`/`_a2`, `a`/`b`, `_1`/`_2`) as one item; the `.md` check uses the group
   name without the side suffix (`1764.md` covers `1764a1`/`1764a2`).
4. Show the items grouped by folder (count and a few names) and ask for tags and people.

## 2. The user provides tags and people

Tags and people are both keywords in `images.keywords` (e.g. `document`, `birth certificate`,
`Fabik Lubica`). Per group or per file the user gives:

- the base tag: `document` (the value this KB uses; `_dokumenty` and `_documents|` are folder-style
  prefixes, so if the user says "dokumenty" confirm they mean `document`);
- a category tag, which picks the skill: `birth certificate`, `death certificate`, `marriage
  certificate` → `extract-register-record`; `census` → `extract-census-record`; any other category
  (school record, letters, postcard, ...) → `extract-generic-document`;
- the people, spelled as in the tree's keywords (check with `find_person`; ask when several fit, never
  pick one). Look at existing values: `select keywords from images` and count them, to match spelling.

Anything the user leaves out goes under "Needs you" with the reason; it is skipped, not guessed.

**Excluded categories:** `travel card`, `resident permit`, `id card`, `driving license`,
`health insurance card`. Never read, tag, extract or hand these to a worker, even when also tagged
`document`. List them as skipped.

## 3. Review gate

Show one table: item, tags, people, planned skill, target `.md`. Say which items are skipped and why.
Say that the tags are written into the image files (with a backup copy), not into a sidecar. Wait for
the user's go; corrections change the table, not the plan. Nothing is written before the go.

## 4. Write the tags, reindex, extract

1. Write the tags INTO each image with exiftool, the way the user's other files carry them (a genea
   `changes apply` sidecar is not enough: the user's photo software does not show it). First copy the
   image and any `.xmp` next to it to `D:\Foto DB\.genea\backups\embed-<YYYYMMDD>\`. Then read what the
   image and its sidecar already hold (`exiftool -s -G1 -XMP-dc:all -XMP-lr:all -IPTC:Keywords`) and
   merge: keep every existing keyword and field (description, rating, ...), add the new ones, never
   drop one. Write all three keyword fields with the merged list (`-XMP-dc:Subject`, `-IPTC:Keywords`
   one `+=` per value) and `-XMP-lr:HierarchicalSubject` as the user's layout shows:
   `_deni|_dokumenty|<category>` for the category, `_deni|<person>` for each person, plus `_deni` and
   `_deni|_dokumenty`. Check against an existing tagged file of the same kind. Use
   `-overwrite_original -m`. If a sidecar exists and everything in it is now in the image, delete it
   (the backup keeps it); a sidecar left behind would override the embedded keywords in the index.
   If a file fails, report it and carry on with the tags held in the session for that file only.
2. Run `genea --kb "D:\Foto DB" index --no-summaries` so the index shows the new keywords, and check
   them with `select keywords from images`.
3. Extract: invoke the skill planned for each item. Several items: background workers as that skill
   describes (Agent tool, `run_in_background: true`, all in one message, a few items each, none twice;
   each prompt repeats "never overwrite an `.md`, always the `Review status: NOT REVIEWED` line, no git
   commands" and names the person from the tags). The register skill reads its target from the file
   name; if the name and the tagged person disagree, the worker reports it, it does not choose.

## Before saying done

One combined report from what the workers reported (never more): per item the `.md` path written,
doubtful values, and name mismatches; then the skipped excluded files and the "Needs you" files, and any
tag change that failed. Do not run git commands or commit.
