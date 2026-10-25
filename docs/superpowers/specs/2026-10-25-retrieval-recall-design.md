# Retrieval recall: finding everything the knowledge base holds about a person

Date: 2026-10-25. Branch: `worktree-improve-retrieval-recall`.

## Intent and why

Coverage of the knowledge base is essentially done: most documents, register pages, census sheets, letters and
chronicle months have an MD file. The remaining problem is retrieval. When the user asks genea for everything
about a person, the answer is built from only part of what is indexed, so the user does not get all the available
information.

A test query ("Anna Fabik") on 2026-10-25 showed four causes:

1. `search_documents` returns at most 8 chunks by default (20 at most) out of 30 candidates per method. A person
   in dozens of documents is sampled, not covered.
2. 8 hits at 1,500 characters of text each overflow `tool_output_chars` (12,000), so the result came back with
   `truncated: true` and the last hit cut. The agent gets fewer hits than asked for and is not told how many are
   missing.
3. Search has no notion of a tree person. It mixed three different Anna Fabiks from the tree, other Fabiks, and
   the census row of a different Anna Fabikova. The index already has a `links` table (chunk or image ->
   `ged_file` + `xref`, with a confidence) built by `rebuild_links`, but `search_documents` does not use it.
4. `name_forms` builds only "given surname", "surname given", "first-given surname", "surname first-given" and
   "initial surname" forms. Slovak feminine surnames (Fábik / Fabiková / Fabíková) and Slovak declined forms of
   both given names and surnames (Anny Fabikovej, Jána Fabika) are different words to the word match, so such mentions get no link to the person.

## Goals

- A new tool, `documents_about(person_id)`, that takes a GEDCOM person id as `find_person` returns it
  (`tree.ged#@I12@`) and returns every document that mentions that person, grouped by file, with a total count.
  The result is never silently capped: if it cannot all fit, it says how many files and chunks remain and how to
  get the next page.
- Better person links: `name_forms` gains Slovak surname variants and Slovak declined forms of given names and
  surnames, so `rebuild_links` links more
  mentions to the right person.
- `search_documents` no longer truncates in normal use, returns at most one top hit per file by default, and
  reports how many more matching files and chunks exist beyond what it returned.
- Both tools are registered in the agent (`agent/core.py`) and the MCP server (`mcp_server.py`), and the agent
  prompt tells the model to use `documents_about` for "everything about X" questions.

## Non-goals

- No change to coverage: no new OCR, extraction or transcription work.
- No change to GEDCOM files, documents or photos; genea stays read-only except for analyses.
- No attempt to resolve which of two same-named people a mention belongs to beyond what is described below.
- No new embedding model, and no change to chunking.

## Anti-goals (a failure even if it technically works)

- A result that looks complete but is not. Every list must state its total and what was left out.
- A mention of a different person with the same name shown as certain. Ambiguous hits must be marked as such.
- Output over `tool_output_chars`: the tool runner then cuts every string to 2,000 characters, which is the bug
  being fixed here.
- Rules 1 and 7 of the genea prompt must keep holding: every hit carries a citation, and summaries are never
  returned as evidence by `documents_about`.

## Constraints

- Python project under `src/genea`, tested with pytest (`uv run pytest`, 712 passing at the baseline). New
  behaviour needs tests, using the sample KB fixtures in `tests/fixtures`.
- Changing `name_forms` changes link content, so existing indexes need `rebuild_links` re-run. It must be
  possible without re-embedding (links are computed from chunk text only).
- Conventions: the person id format is `<ged file>#<xref>` as parsed by `parse_person_id`; results are pydantic
  models in `tools/models.py` carrying `sources`; output is rendered through `render_limited` in
  `tool_runner.py`.
- The user works on a CPU-only machine; no new heavy dependencies.

## Design

### `documents_about(person_id, page=1)`

- Resolve the id with `parse_person_id`. Read `links` rows where `source_kind = 'chunk'` for that person, join to
  `chunks` and `files`. Exclude `kind = 'summary'` chunks (they are already excluded from links, keep it that
  way).
- Group by file. For each file return: path, number of matching chunks, the best confidence, and for each of up
  to a few chunks a short snippet around the first mention (a window of text, not the first 1,500 characters),
  with page / chunk / `start_s` for the citation.
- Order files by best confidence, then by number of matching chunks. Page the result so that one page always
  fits `tool_output_chars` (same approach as `PAGE_SIZE` in `analysis/models.py`). The result states
  `total_files`, `total_chunks` and whether another page exists.
- Mark a hit `ambiguous` when another person in the tree has a name form that matches the same chunk with the
  same confidence (the same-named Annas). The result carries a count of ambiguous files so the model can tell the
  user.
- Images linked to the person are not in this tool; `find_images` already covers them. The result mentions that.

### Name forms

- Extend `name_forms` with surname variants: masculine to feminine (`-ik` -> `-ikova`, `-y` / `-i` -> `-a`) and
  feminine to masculine (`-ova` removed).
- Add Slovak declension (genitive, dative, accusative, locative, instrumental) of both the given name and the
  surname, for men and women: for example Anna -> Anny, Anne, Annu, Annou; Ján -> Jána, Jánovi, Jánom;
  Fabiková -> Fabikovej, Fabikovú, Fabikovou; Fabik -> Fabika, Fabikovi, Fabikom. In a combined form both parts
  take the same case ("Anny Fabikovej", "Jána Fabika"), in either word order.
- Forms are matched after `normalize`, so diacritics are already folded (`Fabikova` covers `Fabiková` and
  `Fabíková`). Folding can make two cases look alike, which is harmless for matching.
- Ján / Jan and other given-name spellings that differ by more than accents are not added; the tree's own name
  variants (`name_variants`) still cover those.
- Variant forms get a lower confidence than exact forms (for example 0.8), so exact-name hits rank first and the
  confidence ordering above stays meaningful.
- The exact rules are for the builder to settle with tests drawn from names in the real tree and documents
  (see "Left to the builder").

### `search_documents`

- Hit text is shortened so that `k` hits always fit in `tool_output_chars` (budget per hit computed from the limit
  and `k`, not a fixed 1,500). The snippet should be centred on the query terms where possible.
- By default at most one hit per file (the best chunk). A parameter allows several chunks per file when the
  agent wants them.
- The result gains `more_files` and `more_chunks` counts: how many matching candidates were found but not
  returned.
- Existing parameters and the ranking (BM25 + vector, reciprocal rank fusion) stay as they are.

### Agent and MCP

- Register `documents_about` next to `search_documents` in `agent/core.py` and `mcp_server.py`, with a docstring
  that says when to use it.
- Add one rule to the agent prompt (`agent/prompt.py`): for questions asking for everything about a person, call
  `find_person`, then `documents_about`, then read the most relevant documents.

## Testing

- Unit tests on the sample KB: a person with several documents returns all of them, grouped; a total larger than
  one page reports the remainder and a second page returns the rest; ambiguous same-name people are marked;
  a summary chunk is never returned.
- Name forms: tests for each variant rule, including the Fábik / Fabiková pair from the real tree, and declined
  given names and surnames for a man and a woman ("Anny Fabikovej", "s Jánom Fabikom") linking to the right person.
  The question the live eval currently misses should pass after a links rebuild.
- `search_documents`: a test that `k = 8` on large chunks does not set `truncated`, that one hit per file is the
  default, and that the more-counts are right.
- The live Q&A eval in `tests/live` gets at least one "everything about <person>" question.
- Manual check after merge: rebuild links on the real KB (`D:\Foto DB`), run `documents_about` for Anna Fabik
  (`I0034`) and compare against what `search_documents` returned.

## Decisions made while writing (author's calls, not the user's)

- **Confidence in `documents_about`:** all tiers are included, so recall is not cut. Each file reports its best
  confidence and is labelled `exact` (1.0), `variant` (spelling variants) or `weak` (initial + surname). Files
  are ordered by best confidence, so weak hits come last.
- **Same-name people:** marking is enough for this change. A hit is `ambiguous` when any other tree person
  matches the same chunk at any confidence. A mention of someone who is not in the tree (such as the census
  Fabikova above) cannot be detected, and that gap is accepted. Narrowing by birth year, place or family context
  is a possible later step and is not built here.
- **`search_documents` counts:** `k` means files by default (one best chunk per file). `more_files` and
  `more_chunks` are documented as lower bounds ("at least"), because candidates stay capped. The candidate cap
  per method may be raised to about 100 so that per-file grouping still leaves `k` files, if speed on CPU allows.
- **Index migration:** every `genea index` run already ends with `rebuild_links`, even when no file changed, so
  the next index run recomputes the links with the new rules (no re-embedding, no version key needed).
- **Output budget:** every snippet plus its metadata stays well under 2,000 characters (aim for about 600), and a
  page stays under `tool_output_chars` with room to spare.
- **Declension (user's decision):** the declension miss in the live eval is Slovak, not Czech. Both given names and
  surnames are declined (see "Name forms"). Whether the declension rules also apply to `search_documents` queries
  (FTS) is left to the builder; links and `documents_about` must have them.
- **Success measure:** the manual Anna Fabik comparison plus the new live eval question; no numeric recall target.

## Left to the builder

- Exact surname-variant rules and the confidence values.
- Snippet size and the number of chunks shown per file.
- Page size, as long as a page never exceeds `tool_output_chars`.
- How ambiguity is detected, as long as same-named tree people are reliably marked.
- Naming of the new result fields, following the existing models.
