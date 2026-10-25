# Compact search results

## Why

`search_documents` hits repeated data: the file path was in every hit's `file` and `source` and again in the result's
`sources`, and `query`, `score`, `end_s` and the default `kind` were never needed by the model. The two sides of one
paper (`..._a.md` / `..._b.md`, `...172a1` / `...172b2`) filled two of the up to 8 file slots.

PR #23 already gave match-centred passages, one hit per file and the `more_files` / `more_chunks` counts, and
fills `tool_output_chars` on purpose (full retrieval recall). This change keeps that and only removes waste.

## Design

- `DocHit` drops `source`, `score` and `end_s` from the JSON (still attributes in Python), and `kind` when it is
  `"text"`. `SearchResult` drops `query`. The result's `sources` list stays: the citation rules rely on it.
- Files that are other sides of a better-ranked file's document fold into that hit's `also` list (paths only) and
  no longer take a slot. Same family = same directory and the same name after removing a trailing `_a`/`_b` (any case)
  or an `a1`/`b2` that follows a digit (`kniha1.pdf` and `tab1.md` are not sides). Other chunks of the *same* file
  are never folded.

## Not changed

Ranking, snippets, `k`, `per_file`, the size fitting and the index. No reindex.

## Earlier idea dropped

A smaller default page (5 hits, 1,500 characters), `offset` paging and a score cutoff were built and reviewed on
this branch before #23 landed. #23 covers paging by `more_files`; the cutoff discarded every hit found by only one
retriever (RRF scores), and a smaller page would work against the recall goal.
