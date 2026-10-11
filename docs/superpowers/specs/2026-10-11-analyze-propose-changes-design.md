# Analyze a family, branch or person and propose changes — Design

Date: 2026-10-11
Status: Draft, awaiting review

## 1. Goal

Given a scope (a person, a family, or a branch of the tree), genea gathers what the index knows,
finds conflicts and gaps, and writes an **iteration of the tree**: a full copy of the indexed `.ged` in
which every proposed change is already applied and marked inside the GEDCOM, plus a Markdown report and
a unified diff. Available from the CLI and MCP.

Phase one (this spec) only writes files under `.genea/analyses/`. It never modifies the real `.ged`,
documents or photos. Phase two (not built here) promotes an iteration to the real tree through the
git-branch/PR workflow on `fam_tree_ged`, followed by the manual Gramps sync (see the write-back design
notes; Gramps stays the master and its GEDCOM import never merges).

Non-goals: deciding which of two conflicting dates is right; editing the real `.ged`; importing an
iteration into Gramps; LLM judgment inside the detectors.

## 2. Scope resolution (`analysis/scope.py`)

`resolve_scope(ctx, kind, id, direction, depth) -> Scope` (people set, family set, boundary people).

- `person <id>`: the person and the families they belong to (as child and as spouse).
- `family <fam-id>`: both spouses, the children, and the parents' families of both spouses.
- `branch <id> --direction ancestors|descendants|both --depth N` (default `both`, depth 4): walks the
  existing `relatives` logic. People at the depth limit are listed as the boundary.

Person ids use the existing `tree.ged#@I12@` form and go through `parse_person_id`. If the index is
missing or incomplete the report says so in its header, and absence of evidence is never reported as a
finding of absence.

## 3. Detectors (`analysis/detectors.py`)

Each detector takes a `Scope` and returns `Finding`s. Deterministic, individually testable.

- **Conflicts.** The existing `find_conflicts` rules (event after death, baptism before birth, parent too
  young/old, married too young, document year mismatch) are refactored to emit findings, keeping their
  conservative date-range semantics. New: siblings under 9 months apart, child born after the mother's
  death, marriage dated after a child's birth (`info`).
- **Gaps.** Missing birth or death for a person who is probably deceased, missing parents, missing sex,
  events without a source citation, people mentioned in linked documents but with no events.
- **Document evidence.** A linked passage gives a date or place the tree lacks (or lacks entirely):
  "document suggests X, tree has Y/nothing".

A `Finding` has: `id` (stable hash of kind + target + payload), `kind`, `severity`
(`error | needs_review | info`), `person_ids`, `message`, `sources` (`SourceRef`s) and
`proposed: {operation, payload} | None`. `proposed` uses the six existing GEDCOM operations
(`add_event`, `update_event`, `add_note`, `add_source_citation`, `link_child`, `add_person`).
It is `None` whenever no safe proposal exists (real conflicts, ambiguous matches).

## 4. The iteration file (`analysis/iteration.py`)

- Built with the lossless parser (`ingest/gedcom/parser.py`, keeps `raw_bytes`): the copy is byte-identical
  to the base except for inserted lines. Encoding, BOM, line endings and unrelated records are untouched.
- Base is the latest iteration for the same scope if one exists and its recorded base hash still matches
  the real `.ged`, otherwise the real `.ged`. If the real file changed since the latest iteration, the
  run starts again from the real file and the report says why.
- Each finding with a `proposed` change is applied to the copy; each is marked with a `NOTE`:
  - event change: `2 NOTE` under that event; otherwise `1 NOTE` on the INDI or FAM record;
  - new person/family: xref `@GENEA<n>@` (no clash with Gramps ids) and a record-level `1 NOTE`;
  - text: `GENEA v1 2026-10-11 f-3a9c: BIRT date changed 1881 -> 1883. Evidence: [docs/matrika.pdf p.3].
    Base value: 1881 [tree.ged:L1042].`
- Findings with no proposal get a `1 NOTE` beginning `GENEA CHECK v1 …` that states the evidence on
  both sides and changes nothing else.
- Citations in notes refer to **base-file** line numbers; the note says "base".
- Long text is split with `CONC`/`CONT` at the 255-character limit. The fixed `GENEA` prefix lets tooling
  find, keep or strip the notes, and lets a re-run recognise notes it already wrote (no duplicates; an
  existing finding id is skipped).
- Iterations are never indexed, so a note can never be read back as evidence. `resolve_inside` already
  rejects paths inside `.genea`.

## 5. Output layout

`.genea/analyses/<scope-slug>-<YYYY-MM-DD>-v<N>.{ged,md,diff}` (`kb.analyses_dir`, added to `SUBDIRS`).

- `.ged`: the iteration.
- `.diff`: unified diff of base vs iteration.
- `.md`: header (scope, base file and hash, index state, counts), then findings grouped by person or
  family: severity, message, citations, and for proposals the diff hunk. A separate section lists findings
  that need a decision. A trailing `notes` section holds Claude's comments added through MCP, never
  mixed into evidence.

## 6. Interfaces

- CLI (`cli/analysiscmds.py`): `genea analyze person|family|branch <id> [--direction] [--depth]`
  writes the three files and prints a summary and path. `genea analysis list` and `genea analysis show
  <name>` read them.
- MCP (`mcp_server.py`): `analyze(scope, id, direction, depth)` writes the files and returns the findings
  and paths; `read_analysis(name)` returns a report; `add_analysis_note(name, text)` appends to the
  report's `notes` section. These are the only MCP tools that write, and only under `.genea/analyses/`
  (name validated against `^[\w.-]+$`, resolved and checked with `is_relative_to(kb.analyses_dir)`).
  The server instructions are updated: the "cannot change any file" rule gets this narrow exception and
  the assistant must still never claim the tree itself was changed.
- Agent (`agent/core.py`): the same `analyze` tool, for parity.

## 7. Phase two (designed for, not built)

`genea analysis promote <name>`: copy the iteration into the `fam_tree_ged` clone on a branch, optionally
strip `GENEA` notes (flag), open a PR via `gh`. After merge, `genea changes export`-style checklist for
entering changes in Gramps, and a verify step comparing the next Gramps export. Nothing in phase one
blocks this: the iteration is already a valid GEDCOM and the diff is the PR content.

## 8. Testing

- Detectors: one small fixture GEDCOM per rule, including borderline ranges that must not fire.
- Scope: person/family/branch walking, depth limit, boundary list.
- Iteration: byte-identical outside inserted lines (including ANSEL/BOM fixtures); each of the six
  operations; note placement; `CONC`/`CONT` splitting; idempotent re-run; stale-base restart.
- Findings: id stability across runs.
- Security: MCP name/path traversal cannot write outside `.genea/analyses/`; the real `.ged` hash is
  unchanged after every analyze.
- CLI and MCP smoke tests following `test_cli_query.py` and the MCP tests.

## 9. Open points

- Final rule list for gaps (the initial set above can grow later without changing the format).
- Whether `promote` strips notes by default (phase two decision).
