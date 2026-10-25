# Retrieval Recall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** genea returns everything the knowledge base holds about a tree person: a new `documents_about` tool,
person links that understand Slovak surname variants and declension, and a `search_documents` that never silently
truncates.

**Architecture:** Declension and name-form generation feed the existing `links` table (rebuilt on every
`genea index` run). `documents_about` is a paged query over `links` + `chunks`, with query-time ambiguity
detection. `search_documents` keeps its BM25 + vector RRF ranking but groups by file, sizes snippets to the output
budget and reports what it left out.

**Tech Stack:** Python 3, SQLite (FTS5, sqlite-vec), pydantic, pydantic-ai agent, MCP server, pytest (`uv run pytest`).

**Spec:** `docs/superpowers/specs/2026-10-25-retrieval-recall-design.md`

## Global Constraints

- Every result stays under `tool_output_chars` (default 12,000) in normal use; over it, `render_limited` cuts every
  string to 2,000 characters, which is the bug being fixed.
- Person ids are `<ged file>#<xref>`, resolved with `parse_person_id` (`genea.tools.base`).
- Results are pydantic models in `src/genea/tools/models.py` deriving from `ToolResult` and carrying `sources`.
- Summary chunks (`kind = 'summary'`) are never returned by `documents_about` (they never get links today; keep it).
- No schema change and no re-embedding: links are recomputed by the existing `rebuild_links` call at the end of every
  `genea index` run (`indexer.py:434-436`), even when no file changed. That replaces the spec's "name-form version"
  idea: no version key is needed. (Author's call: the existing behaviour already gives the migration.)
- Confidence tiers: exact form 1.0, variant form (gender-switched or declined) 0.8, initial + surname 0.6. Labels
  `exact` / `variant` / `weak`.
- Declension is Slovak: nominative, genitive, dative, accusative, locative, instrumental. Both parts of a two-word
  form take the same case. Vocative is not generated (author's call: Slovak letters use the nominative).
- Tool docstrings in `agent/core.py` and `mcp_server.py` must be identical (`test_descriptions_match_the_agent_tools`).
- No new dependencies.

## Review Focus

1. A chunk that names two different relatives (father and son) must not mark either as ambiguous; only a shared
   name form does. Test in Task 4.
2. A declined form of one name equal to another person's nominative ("Jána" folds to "jana") must not link a
   one-word form; forms stay two words or more. Test in Task 2.
3. A person with no given name or no surname (common in the tree) gets no forms (test in Task 2), and a person with
   no linked documents gets an empty, well-formed `documents_about` result with total 0, not an error (Task 4).
4. `page` beyond the last page returns a `ToolError` naming the number of pages. Test in Task 4.
5. `search_documents` with `k=20, per_file=5` on long chunks still fits the limit. Test in Task 5.

---

### Task 1: Slovak declension

**Files:**
- Create: `src/genea/declension.py`
- Test: `tests/test_declension.py`

**Interfaces:**
- Produces: `CASES = ("nom", "gen", "dat", "acc", "loc", "ins")`;
  `given_cases(given: str, sex: str | None) -> dict[str, set[str]]` and
  `surname_cases(surname: str, sex: str | None) -> dict[str, set[str]]`. Inputs and outputs are single
  *normalized* words (`genea.text.normalize`: lowercase, accents folded). Every case key is present; `"nom"` contains
  the input. `sex` is `"M"`, `"F"` or `None`; `None` returns the union of the M and F rules.

The ending rules, applied to normalized words (author's calls where the spec leaves them; over-generation is
acceptable, a missed common form is not):

| Word | gen | dat | acc | loc | ins |
|---|---|---|---|---|---|
| F given `-ia` (maria) | `-ie` | `-ii` | `-iu` | `-ii` | `-iou` |
| F given other `-a` (anna) | `-y`, `-e` | `-e` | `-u` | `-e` | `-ou` |
| F surname `-a` (fabikova, rumanovska) | `-ej` | `-ej` | `-u` | `-ej` | `-ou` |
| F word not ending in `-a` (krejci, ruth) | unchanged in every case |
| M surname `-y`/`-i` (rumanovsky) | `-eho` | `-emu` | `-eho` | `-om` | `-ym` |
| M word `-a` (kubica) | `-u` | `-ovi` | `-u` | `-ovi` | `-om` |
| M word `-o` (belinko) | `-a` | `-ovi` | `-a` | `-ovi` | `-om` |
| M word ending in a consonant (fabik, jan, jozef) | `+a` | `+ovi` | `+a` | `+ovi` | `+om` |

Masculine words ending `-ek`/`-ec` also get the fleeting-vowel forms (`ouzednicek` -> `ouzednicka`,
`ouzednickovi`, `ouzednickom`; `moncek` -> `moncka`), in addition to the full-stem forms. The M given-name rules are
the M-word rows; the adjectival `-y`/`-i` row applies to surnames only.

- [ ] **Step 1: Write the failing tests**

```python
from genea.declension import CASES, given_cases, surname_cases

def test_feminine_given_name():
    c = given_cases("anna", "F")
    assert set(c) == set(CASES) and "anna" in c["nom"]
    assert {"anny", "anne", "annu", "annou"} <= c["gen"] | c["dat"] | c["acc"] | c["ins"]
    assert "marie" in given_cases("maria", "F")["gen"] and "mariou" in given_cases("maria", "F")["ins"]

def test_masculine_given_name():
    c = given_cases("jan", "M")
    assert "jana" in c["gen"] and "janovi" in c["dat"] and "janom" in c["ins"]

def test_feminine_surname():
    c = surname_cases("fabikova", "F")
    assert "fabikovej" in c["gen"] and "fabikovu" in c["acc"] and "fabikovou" in c["ins"]
    assert surname_cases("krejci", "F")["ins"] == {"krejci"}

def test_masculine_surnames():
    assert "fabika" in surname_cases("fabik", "M")["gen"] and "fabikom" in surname_cases("fabik", "M")["ins"]
    assert "rumanovskeho" in surname_cases("rumanovsky", "M")["gen"]
    assert "ouzednicka" in surname_cases("ouzednicek", "M")["gen"]

def test_unknown_sex_is_the_union():
    c = surname_cases("fabik", None)
    assert "fabika" in c["gen"]
```

- [ ] **Step 2:** Run `uv run pytest tests/test_declension.py -v`. Expected: FAIL, `ModuleNotFoundError: genea.declension`.
- [ ] **Step 3:** Implement `given_cases` and `surname_cases` in `src/genea/declension.py` from the table above.
- [ ] **Step 4:** Run `uv run pytest tests/test_declension.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit: `git add src/genea/declension.py tests/test_declension.py` and `git commit -m "Add Slovak declension of given names and surnames"`.

### Task 2: Name forms with gender variants and declension

**Files:**
- Modify: `src/genea/index/indexer.py:231-274` (`name_forms`, `rebuild_links`)
- Test: `tests/test_indexer.py:122-128`, `tests/test_links.py`

**Interfaces:**
- Consumes: `given_cases`, `surname_cases` (Task 1).
- Produces: `name_forms(given: str | None, surname: str | None, variants: list[str], sex: str | None = None)
  -> list[tuple[str, float]]`, plus constants `EXACT = 1.0`, `VARIANT = 0.8`, `WEAK = 0.6` in `indexer.py`.
  `rebuild_links` reads `sex` from `persons` and passes it.

Behaviour: the existing forms stay at `EXACT` / `WEAK` (unchanged). New `VARIANT` forms: for each case, every
given-name form of that case combined with every surname form of the same case, in both word orders, for the full
given name and its first word. Gender-switched surnames (masculine `-ik` -> `-ikova`, `-y`/`-i` -> `-a`, any
masculine consonant-final -> `+ova`; feminine `-ova` -> stem, `-a` after `sk`/`ck` -> `-y`) are declined with the
opposite sex's rules and added at `VARIANT`. An existing form never has its confidence lowered (`setdefault`-style,
as today). Forms are always at least two words. Tree `name_variants` are added as today, not declined.

- [ ] **Step 1: Write the failing tests** (extend `test_name_forms`, add to `tests/test_links.py`)

```python
def test_name_forms_slovak_variants():
    forms = dict(name_forms("Anna", "Fábik", [], "F"))
    assert forms["anna fabik"] == 1.0
    assert forms["anna fabikova"] == 0.8 and forms["fabikovej anny"] == 0.8
    assert forms["anny fabikovej"] == 0.8
    assert "anny fabik" not in forms                     # both parts take the same case
    assert all(len(f.split()) >= 2 for f in forms)
    assert dict(name_forms("Ján", "Fabik", [], "M"))["janom fabikom"] == 0.8
    assert name_forms(None, "Fabik", [], "M") == [] and name_forms("Anna", None, [], "F") == []

def test_declined_mention_links_to_the_person(tools):    # kronika: "oženil s Annou Svobodovou"
    rows = tools.conn.execute(
        "SELECT l.confidence FROM links l JOIN chunks c ON l.source_kind = 'chunk' AND c.id = l.source_id "
        "JOIN files f ON f.id = c.file_id WHERE f.path = 'docs/kronika.txt' AND l.person_xref = '@I2@'").fetchall()
    assert [r[0] for r in rows] == [0.8]
```

  In `tests/test_links.py`, pass `r["sex"]` to `name_forms` in `_reference_links` (select `sex` too) and insert a
  random `sex` (`"M"`, `"F"`, `None`) per person, so the fast algorithm is still checked against the exhaustive one.

- [ ] **Step 2:** Run `uv run pytest tests/test_indexer.py::test_name_forms_slovak_variants tests/test_links.py -v`. Expected: FAIL (`KeyError: 'anna fabikova'`, no link row).
- [ ] **Step 3:** Implement the new `name_forms` signature and pass `sex` in `rebuild_links`.
- [ ] **Step 4:** Run `uv run pytest tests/test_indexer.py tests/test_links.py tests/test_conflicts.py -v`. Expected: PASS (conflicts uses links as evidence and must not regress).
- [ ] **Step 5:** Commit: "Link Slovak declined and gender-variant name forms to tree people".

### Task 3: Snippet around a match

**Files:**
- Modify: `src/genea/text.py`
- Test: `tests/test_text.py`

**Interfaces:**
- Produces: `snippet(text: str, needles: list[str], width: int) -> str`. `needles` are normalized strings (one or
  more words). It returns at most `width` characters of the *original* text, centred on the earliest whole-word
  occurrence of any needle (compared after normalization), with `…` added at each end that was cut. With no
  occurrence, it returns the start of the text cut to `width` (with `…`).

Approach: normalize the original one character at a time with the same rules as `normalize`, keeping a list that
maps each normalized character back to its original index, then find needles on word boundaries in the normalized
string and map the span back.

- [ ] **Step 1: Write the failing tests**

```python
def test_snippet_centres_on_a_declined_name():
    text = "x " * 300 + "Jozef sa oženil s Annou Fabíkovou v Turnej. " + "y " * 300
    s = snippet(text, ["annou fabikovou"], 120)
    assert "Annou Fabíkovou" in s and len(s) <= 120 and s.startswith("…") and s.endswith("…")

def test_snippet_without_match_returns_the_start():
    assert snippet("Kronika obce Brno. " * 20, ["nikto"], 50).startswith("Kronika obce")

def test_snippet_matches_whole_words_only():
    assert "Annou" not in snippet("Hannou " * 50 + "end", ["annou"], 30)
```

- [ ] **Step 2:** Run `uv run pytest tests/test_text.py -v`. Expected: FAIL, `ImportError: snippet`.
- [ ] **Step 3:** Implement `snippet`.
- [ ] **Step 4:** Run `uv run pytest tests/test_text.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit: "Add snippet: original text around a normalized match".

### Task 4: `documents_about`

**Files:**
- Create: `src/genea/tools/mentions.py`
- Modify: `src/genea/tools/models.py` (new models after `SearchResult`)
- Test: `tests/test_mentions.py`

**Interfaces:**
- Consumes: `name_forms`, `EXACT`, `VARIANT` (Task 2); `snippet` (Task 3); `parse_person_id`, `summary`
  (`genea.tools.people`).
- Produces: `documents_about(ctx: ToolContext, person_id: str, page: int = 1) -> PersonDocuments`, and models:

```python
class Mention(BaseModel):
    page: int | None = None
    chunk: int
    start_s: float | None = None
    snippet: str

class DocumentMentions(BaseModel):
    file: str
    match: Literal["exact", "variant", "weak"]
    confidence: float
    chunks: int                 # matching chunks in this file
    ambiguous: bool
    mentions: list[Mention]     # at most MENTIONS_PER_FILE
    source: SourceRef

class PersonDocuments(ToolResult):
    person: PersonSummary
    total_files: int
    total_chunks: int
    ambiguous_files: int
    page: int
    pages: int
    files: list[DocumentMentions]
    note: str
```

Values: `FILES_PER_PAGE = 6`, `MENTIONS_PER_FILE = 3`, `SNIPPET_CHARS = 240`. Files are ordered by confidence
(descending), then matching chunks (descending), then path. Within a file, mentions follow page / start_s / chunk
order. `sources` holds one `SourceRef` per file on the page (the first mention's page or start_s). `note` always
says that photos are found with `find_images` and, when `ambiguous_files > 0`, that ambiguous files may be about
another person with the same name. With zero links: `total_files = 0`, `pages = 1`, `files = []`. A `page` outside
1..`pages` raises `ToolError(f"page must be between 1 and {pages}")`. Snippets centre on the person's own name forms.

Ambiguity (query time, no schema change): a chunk is ambiguous when another person linked to the same chunk has a
name form in common with this person and that form occurs in the chunk's normalized text. A file is ambiguous
when any of its matching chunks is.

- [ ] **Step 1: Write the failing tests** (sample KB: `@I3@` and `@I12@` are both "Jan Novák"; `@I1@` Josef Novák
  is his father; `@I2@` Anna Svobodová appears declined in `docs/kronika.txt`)

```python
def test_lists_every_linked_document_grouped_by_file(tools):
    r = documents_about(tools, "tree.ged#@I3@")
    files = [f.file for f in r.files]
    assert "docs/kronika.txt" in files and "docs/matrika.pdf" in files
    assert len(files) == len(set(files)) == r.total_files
    assert r.sources and all(f.mentions for f in r.files)

def test_same_name_people_are_ambiguous_but_relatives_are_not(tools):
    jan = documents_about(tools, "tree.ged#@I3@")
    assert all(f.ambiguous for f in jan.files) and jan.ambiguous_files == jan.total_files
    # the chronicle also names Josef and Jan Novák, but Anna Svobodová has no namesake
    anna = documents_about(tools, "tree.ged#@I2@")
    assert not next(f for f in anna.files if f.file == "docs/kronika.txt").ambiguous

def test_declined_mention_is_a_variant(tools):
    r = documents_about(tools, "tree.ged#@I2@")
    kronika = next(f for f in r.files if f.file == "docs/kronika.txt")
    assert kronika.match == "variant" and "Annou Svobodovou" in kronika.mentions[0].snippet

def test_person_without_documents(tools):
    r = documents_about(tools, "tree.ged#@I20@")
    assert (r.total_files, r.pages, r.files) == (0, 1, [])

def test_pages_cover_everything_and_fit_the_limit(tools):
    # write 15 long files that mention Terezie Dvořáková, re-index, then page through
    ...  # build files in tools.kb.root / "docs", call index_kb(tools.kb, FakeEmbedder())
    first = documents_about(tools, "tree.ged#@I6@")
    seen = [f.file for p in range(1, first.pages + 1) for f in documents_about(tools, "tree.ged#@I6@", p).files]
    assert len(seen) == len(set(seen)) == first.total_files >= 16
    assert len(render(tools, first)) <= tools.kb.settings.tool_output_chars
    with pytest.raises(ToolError, match="between 1 and"):
        documents_about(tools, "tree.ged#@I6@", first.pages + 1)

def test_summaries_are_never_returned(...)
    # reuse the transcript fixture from tests/test_transcript_indexing.py; no file entry comes from a summary chunk
```

  The two `...` bodies are the implementer's: the long files use 3,000+ characters of filler with the name in the
  middle and in a second chunk; the summary test asserts no `Mention` maps to a chunk with `kind = 'summary'`.

- [ ] **Step 2:** Run `uv run pytest tests/test_mentions.py -v`. Expected: FAIL, `ModuleNotFoundError: genea.tools.mentions`.
- [ ] **Step 3:** Add the models, then implement `documents_about` in `src/genea/tools/mentions.py`.
- [ ] **Step 4:** Run `uv run pytest tests/test_mentions.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit: "Add documents_about: every document linked to a tree person, paged".

### Task 5: `search_documents` fits, groups by file and reports what is left

**Files:**
- Modify: `src/genea/tools/search.py:17-58`, `src/genea/tools/models.py` (`SearchResult`)
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `snippet` (Task 3), `_fit` / `_json_len` (already in `search.py`).
- Produces: `search_documents(ctx: ToolContext, query: str, k: int = 8, per_file: int = 1) -> SearchResult`;
  `SearchResult` gains `more_files: int = 0` and `more_chunks: int = 0` (lower bounds, documented in the field
  comment as "at least").

Behaviour: `CANDIDATES = 100`. `k` (clamped 1..20) counts files; each file contributes its best `per_file` (clamped
1..5) chunks in score order. `more_files` = candidate files not returned; `more_chunks` = candidate chunks not
returned. Hit text = `snippet(row text, query tokens normalized, width)` with
`width = min(1500, max(200, (tool_output_chars - 600) // hits - 400))`, then `_fit` to that width so escaping cannot
overflow. `_fts_query` emits a prefix query (`"fabik"*`) for tokens of 4+ characters and a plain phrase for shorter
ones, so declined and feminine forms match the full-text side (author's call; the spec left query-side declension to
the builder). Existing ranking (RRF over BM25 and vector) is unchanged.

- [ ] **Step 1: Write the failing tests**

```python
def test_default_search_fits_and_is_one_hit_per_file(tools):
    for i in range(12):
        (tools.kb.root / "docs" / f"dlhy_{i}.txt").write_text("Jozef Fabik oral pole. " * 300, encoding="utf-8")
    index_kb(tools.kb, FakeEmbedder())
    result = search_documents(tools, "Jozef Fabik")
    assert len(result.hits) == 8 and len({h.file for h in result.hits}) == 8
    assert result.more_files >= 4
    out = render(tools, result)
    assert len(out) <= tools.kb.settings.tool_output_chars and '"truncated":true' not in out

def test_search_largest_request_still_fits(tools):        # same files as above
    out = render(tools, search_documents(tools, "Jozef Fabik", k=20, per_file=5))
    assert len(out) <= tools.kb.settings.tool_output_chars

def test_prefix_matches_declined_forms(tools):
    (tools.kb.root / "docs" / "list.txt").write_text("Pozdravujem pani Fabikovej.", encoding="utf-8")
    index_kb(tools.kb, FakeEmbedder())
    result = search_documents(replace(tools, embedder=BrokenEmbedder()), "Fabik")
    assert "docs/list.txt" in {h.file for h in result.hits}
```

  Existing tests in `test_search.py` and `test_transcript_indexing.py` must still pass unchanged.

- [ ] **Step 2:** Run `uv run pytest tests/test_search.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement the changes in `search.py` and `SearchResult`.
- [ ] **Step 4:** Run `uv run pytest tests/test_search.py tests/test_transcript_indexing.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit: "search_documents: one hit per file, snippets sized to the output limit, counts of what is left".

### Task 6: Register the tools, prompt, eval and README

**Files:**
- Modify: `src/genea/agent/core.py:69-72`, `src/genea/mcp_server.py:129-132`, `src/genea/agent/prompt.py:10-12`,
  `tests/live/test_live_qa.py` (`QUESTIONS`), `README.md` (tool list)
- Test: `tests/test_mcp_server.py`, `tests/test_prompt.py`

**Interfaces:**
- Consumes: `documents_about` (Task 4), new `search_documents` signature (Task 5).
- Produces: agent and MCP tools `documents_about(person_id: str, page: int = 1)` and
  `search_documents(query: str, k: int = 8, per_file: int = 1)`.

Exact copy:
- `documents_about` docstring: "Every text document that mentions a person (an id from find_person), grouped by
  file with passages to cite, best matches first. Use it when asked for everything about someone; it says how many
  files there are and whether more pages exist (page=2, ...). Files marked ambiguous may be about a namesake."
- `search_documents` docstring: keep the current text and append " Returns one passage per file unless per_file is
  higher; more_files and more_chunks say how many more matches exist at least."
- Rule 2 of `_HEAD` gains the sentence: "For everything about a person, call documents_about with the id and page
  through all of it before answering."
- Live eval question: `("What do the documents say about Anna Svobodová?", ["1845"])`.

- [ ] **Step 1: Update the tests:** add `"documents_about"` to `READ_ONLY` in `tests/test_mcp_server.py`; add
  `("documents_about", {"person_id": JAN}, mentions.documents_about, (JAN, 1))` to `CASES`; change the
  `search_documents` case to `{"query": "Josef", "k": 3}` -> positional `("Josef", 3, 1)`; in `tests/test_prompt.py`
  assert `"documents_about"` is in both `SYSTEM_PROMPT` and `MCP_INSTRUCTIONS`.
- [ ] **Step 2:** Run `uv run pytest tests/test_mcp_server.py tests/test_prompt.py tests/test_agent.py -v`. Expected: FAIL on the missing tool.
- [ ] **Step 3:** Register the tool in both places with the docstrings above, add `per_file` to both `search_documents` wrappers, add the prompt sentence, the live question, and a `documents_about` line to the README tool list.
- [ ] **Step 4:** Run the full suite `uv run pytest -q`. Expected: all pass (712 baseline + new tests), 2 skipped.
- [ ] **Step 5:** Commit: "Expose documents_about in the agent and MCP server".

### Task 7: Manual check on the real KB (user present)

- [ ] **Step 1:** `uv run genea index --kb "D:\Foto DB"`. Expected: finishes with the "linking" phase; no rebuild prompt.
- [ ] **Step 2:** In Claude Code with the genea MCP server, call `documents_about` for `rodinna historia/fam_tree_ged/latest_jrumanovsky.ged#@I0034@` (Anna Fábik, 1928) and compare with `search_documents("Anna Fabik")`: expect more files, Fabiková / declined mentions present, the other two Annas' records marked ambiguous.
- [ ] **Step 3:** Report the counts to the user in one or two lines.
