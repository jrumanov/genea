# Analyze a family, branch or person and propose changes — Design

Date: 2026-10-11
Status: Draft, awaiting review (revised after design recheck: proposals come from Claude/user, iterations always
rebuilt from the real tree, encoding, stable ids, output size, GENEA notes skipped by the indexer)

## 1. Goal

Given a scope (a person, a family, or a branch of the tree), genea gathers what the index knows and finds
conflicts and gaps. Claude (through MCP) or the user (through the CLI) then records **proposals** for those
findings, with citations. genea builds an **iteration of the tree** from them: a full copy of the indexed
`.ged` with every proposal applied and marked inside the GEDCOM, plus a Markdown report and a unified diff.

Phase one (this spec) writes files only under `.genea/analyses/`. It never modifies the real `.ged`,
documents or photos. Phase two (not built here) promotes an iteration to the real tree through the
git-branch/PR workflow on `fam_tree_ged`, followed by the manual Gramps sync (Gramps stays the master and its
GEDCOM import never merges, so an iteration is for review and the PR, not for importing into Gramps).

Division of labour: **detectors find, proposers decide.** Detectors are deterministic and never guess a value.
Judgment ("is this passage about the same person, which date is right") belongs to Claude or the user and is
recorded as a proposal with its own citations and rationale.

Non-goals: editing the real `.ged`; importing into Gramps; writing ANSEL files; a tool for the local pydantic
agent (qwen3:8b is not good enough at proposals; can be added later).

## 2. Scope resolution (`analysis/scope.py`)

`resolve_scope(ctx, kind, id, direction, depth) -> Scope` with the people, families and boundary people.
A scope always lies inside one `.ged` file.

- `person <id>`: the person and the families they belong to (as child and as spouse).
- `family <fam-id>`: both spouses, the children, and the parents' families of both spouses
  (`parse_family_id`).
- `branch <id> --direction ancestors|descendants|both --depth N` (default `both`, depth 4, max 6 like
  `relatives`): the same walk as `relatives`. People at the depth limit are listed as the boundary.

If the index is missing or incomplete the report says so in its header, and absence of evidence is never
reported as a finding of absence.

## 3. Detectors (`analysis/detectors.py`)

Each detector takes a `Scope` and returns `Finding`s. All date comparisons keep the conservative range
semantics of `tools/conflicts.py`: a rule fires only if it is violated for every date inside the ranges.

- **Conflicts** (severity `error`): the existing `find_conflicts` rules (event after death, baptism before
  birth, parent too young / mother too old, married too young), refactored so `find_conflicts` and the
  detectors share one implementation. New: child born after the mother's death (`error`); siblings born more
  than 1 week and less than 9 months (270 days) apart (`needs_review`, since a premature birth is possible;
  the week excludes twins). Both compare `BIRT` events only: a baptism after the mother's death is normal.
- **Gaps** (severity `info`): no birth/baptism event; no death/burial for someone born more than 110 years
  ago; no parent family; no sex; an event with an empty `source_citations`.
- **Document evidence** (severity `needs_review`): linked passages (`links`, confidence ≥ 1.0) whose years
  match none of the person's events (the existing `document_mismatch`), and linked passages for a person
  with no dated events at all. The finding cites the passage; it never proposes a value.

A `Finding` has `id`, `kind`, `severity`, `person_ids`, `family_id`, `message`, `sources` (`SourceRef`s).
`id` is a short hash of kind + person/family + a key naming what it concerns (the events by type and raw
date, or the passage by file and chunk), never of message, evidence or line numbers, so it stays stable
when evidence changes and when a new Gramps export shifts the lines.

## 4. Proposals (`analysis/store.py`)

A proposal is a change Claude or the user wants made, attached to a finding (or free-standing, `finding_id`
null, for something no detector caught):

`Proposal: id, finding_id | None, author ("claude" | "user"), operation, payload, anchor | None, sources,
rationale, created, status (active | withdrawn | stale)`

- `operation`/`payload` use the six existing GEDCOM operations (`add_event`, `update_event`, `add_note`,
  `add_source_citation`, `link_child`, `add_person`) and are validated with the existing `validate_change`
  rules. At least one source citation and a rationale are required.
- `anchor` (for `update_event`): the raw text of the base line being changed. If the real `.ged` changes,
  the build re-finds the line by its anchor inside the same record; if it is gone or ambiguous the proposal
  becomes `stale` and is reported, never applied blindly.
- The user can **dismiss** a finding (with an optional reason). Dismissals and proposals are keyed by
  finding id and survive re-runs of `analyze`. A proposal whose finding disappears on a re-run stays active
  and is listed as "finding no longer reported".

## 5. The iteration (`analysis/iteration.py`)

`build(analysis)` always starts from the **real** `.ged` (never from a previous iteration) and applies all
active proposals. `vN` is a counter only.

- Uses the lossless parser (`ingest/gedcom/parser.py`). Lines are only **inserted or replaced** (an
  `update_event` replaces a `DATE`/`PLAC` line); every other byte is kept, including BOM and line endings.
  New lines use the file's encoding and line ending. UTF-8 and ANSI files are supported; ANSEL files are
  refused with a clear message (`ansel.py` only decodes).
- Every applied proposal is marked with a `NOTE`: under the changed event (`2 NOTE`) for event changes,
  otherwise on the INDI/FAM record (`1 NOTE`). Text:
  `GENEA v3 2026-10-11 p-7d21 (claude, finding f-3a9c): BIRT date 1881 -> 1883. Evidence: [docs/matrika.pdf
  p.3]. Base value: 1881 [tree.ged:L1042, base file].`
- New people/families get xrefs `@GENEA<n>@`, unique in the file and never clashing with Gramps ids.
- Unresolved findings (open, no active proposal, not dismissed) with severity `error` or `needs_review` get a
  `1 NOTE` starting `GENEA CHECK v3 …` stating the evidence. Gaps (`info`) are only in the report, to keep
  the tree readable.
- Notes are split with `CONT` at newlines and `CONC` at 248 characters, never at a space and never inside a
  UTF-8 character (GEDCOM 5.5.1: importers may trim trailing spaces).
- Citations inside notes use base-file line numbers and say so.
- `GENEA` notes must never become evidence: iterations live in `.genea` (not indexed, `resolve_inside`
  rejects it), and the GEDCOM mapper skips any `NOTE` whose text starts with `GENEA`, so a promoted tree
  stays safe in phase two.

## 6. Files

One directory per scope: `.genea/analyses/<scope-slug>/` (`kb.analyses_dir`, added to `SUBDIRS`).

- `analysis.json`: scope, base file and hash, index state, findings, proposals, dismissals.
- `report.md`: findings grouped by person/family with severity, message, citations, and the proposal (if
  any), plus a section "needs a decision" and Claude's notes. Rewritten by every `analyze` and `build`.
- `vN.ged`, `vN.diff`, `vN.md`: one iteration, its unified diff against the real `.ged`, and the report as
  of that build with each proposal's diff hunk embedded.

Writes are atomic (temp file + `os.replace`), like the change store.

## 7. Interfaces

CLI (`cli/analysiscmds.py`):
- `genea analyze person|family|branch <id> [--direction] [--depth]` — run detectors, write
  `analysis.json` and `report.md`, print counts and the path.
- `genea analysis list | show <name> [--severity] [--page]`
- `genea analysis propose <name> [--finding <id>] --operation <op> --payload '<json>' --source <cite>…
  --rationale <text>`; `withdraw <name> <proposal-id>`; `dismiss <name> <finding-id> [--reason]`
- `genea analysis build <name>` — write `vN.ged`, `vN.diff`, `vN.md`.

MCP (`mcp_server.py`):
- `analyze(scope, id, direction, depth)` — returns counts, the analysis name and the first findings only;
  `read_analysis(name, severity, page)` pages through the rest. Every result stays under
  `tool_output_chars`.
- `propose(name, finding_id, operation, payload, sources, rationale)`, `withdraw(name, proposal_id)`,
  `add_analysis_note(name, text)`, `build_iteration(name)`.
- No `dismiss` over MCP: dismissing is the user's decision.
- These are the only writing MCP tools and they write only inside `.genea/analyses/`: names must match
  `^[\w.-]+$` and the resolved path is checked with `is_relative_to(kb.analyses_dir)`.
- The server instructions get a narrow exception to rule 4: Claude may record proposals and build
  iterations, must cite evidence for every proposal, and must never claim the family tree itself was
  changed.

## 8. Phase two (designed for, not built)

`genea analysis promote <name> [--keep-notes]`: copy the latest iteration into the `fam_tree_ged` clone
on a branch, strip `GENEA` notes unless kept, open a PR via `gh`; after merge a checklist for entering the
changes in Gramps and a verify step against the next Gramps export. Phase one already provides a valid
GEDCOM, the diff for the PR, and the indexer rule that keeps `GENEA` notes out of evidence.

## 9. Testing

- Detectors: a small fixture GEDCOM per rule, including borderline ranges and twins that must not fire;
  `find_conflicts` output unchanged after the refactor (existing `test_conflicts.py` passes).
- Scope: person/family/branch walks, depth limit, boundary list, cycles.
- Store: finding-id stability when evidence changes; dismissals and proposals survive re-runs; validation
  rejects proposals without sources.
- Iteration: unchanged bytes outside inserted/replaced lines (UTF-8 with BOM, CRLF, ANSI fixtures); ANSEL
  refused; each of the six operations; note placement; `CONC`/`CONT` splitting with multi-byte text;
  anchor re-finding and `stale` marking after the real `.ged` changes; `@GENEA<n>@` uniqueness.
- Indexer: a `NOTE` starting with `GENEA` is not indexed as a person note.
- Safety: MCP name/path traversal cannot write outside `.genea/analyses/`; the real `.ged` hash is unchanged
  after `analyze`, `propose` and `build`.
- Output size: a large branch result stays under `tool_output_chars` and pages correctly.
- CLI and MCP smoke tests following the existing CLI and MCP tests.

## 10. Open points

- Whether `promote` strips notes by default (phase two).
- Further gap rules can be added later without changing the file format.
