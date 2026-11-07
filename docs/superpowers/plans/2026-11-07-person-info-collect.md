# Person Info-Collect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Claude collect everything Genea holds on one person or family, propose only what the Gramps tree lacks (events, residences, notes, each with its source at the end of a note), flag conflicts and leads, and have the user review one bundle per person as a PR before anything is written.

**Architecture:** Reuse the analysis pipeline (`analyze` → `propose` → `build_iteration` → apply → snapshot). Extend proposals with family owners, event notes, addresses and conflict decisions; make the Gramps writer create missing places and revert fully on failure. Add a review step that opens a PR in the tree repo holding an editable bundle file; apply reads the PR branch's bundle, then pushes the before/after snapshots onto that same branch. A new skill drives Claude through the collection.

**Tech Stack:** Python 3.11+, pydantic, httpx, typer, pytest, git + `gh`; Gramps Web REST API.

**Spec:** `docs/superpowers/specs/2026-11-07-person-info-collect-design.md`

## Global Constraints

- Never use, read, upload or extract files tagged `travel card`, `resident permit`, `id card`, `driving license` or `health insurance card`, even if also tagged otherwise.
- Sources are text at the end of the note: a note ends with its source, an event gets an attached note ending with its source. This flow creates no Gramps Citation objects.
- Residence events carry the value `Address: <Address>` (Gramps event description), dated when a date is known.
- Only the chosen person's (or family's) own data is written; other people go into a leads note on the chosen person.
- Conflicts are never auto-resolved; "replace" adds the new value and marks the old one, never deletes or edits it; the old record keeps its citations.
- A re-run must not duplicate events, notes or places.
- Keep pytest parametrize ids short (Windows path length).

## Review Focus

1. A bundle file edited in an unexpected way (unknown decision word, a section header mangled, an id that is not in the analysis): apply must refuse with a message naming the line, writing nothing.
2. Two Gramps places with the same title as a new Residence's place: use the existing "not unique" error, never create a third.
3. A failure on the fifth item of a bundle: every earlier item of that proposal is reverted, earlier proposals stay recorded as written, and the review branch is left consistent (no half-pushed snapshot commit).
4. A re-run after the user retyped spacing in a Genea note in Gramps: compare note bodies with whitespace normalised, so the note is not added again.
5. A family run where the family's Gramps id no longer exists: the family proposals are stale, not failed, and the member bundles are unaffected.

---

### Task 1: Exclude identity documents in code

**Files:**
- Create: `src/genea/tools/excluded.py`
- Modify: `src/genea/tools/mentions.py`, `src/genea/tools/search.py`
- Test: `tests/test_excluded.py`

**Interfaces:**
- Produces: `EXCLUDED_TAGS: frozenset[str]` (the five tags, lower-case); `excluded_paths(conn) -> set[str]` — KB-relative paths of every image whose `images.keywords` holds an excluded tag (case-insensitive), plus every `.md` in the same folder whose stem equals the image stem or is a prefix of it followed by `_` (the extract skills name a multi-part document's MD without the `_a1` suffix).

- [ ] **Step 1: Write failing tests** — `test_image_with_tag_excluded`, `test_md_next_to_tagged_image_excluded` (`x_a1.jpg` tagged `id card`, `x.md` beside it → both in the set), `test_untagged_kept`, and `test_documents_about_skips_excluded`, `test_search_skips_excluded`, `test_find_images_skips_excluded`, `test_read_document_refuses_excluded` (raises `ToolError` whose message contains `excluded`). Build the index rows with the existing test helpers used in `tests/test_mentions.py`.
- [ ] **Step 2: Run** `pytest tests/test_excluded.py -v` — expect FAIL (module missing).
- [ ] **Step 3: Implement** `excluded.py`; filter rows by path in `documents_about`, `search_documents`, `find_images`; `read_document` raises `ToolError("<path> is an excluded identity document")`.
- [ ] **Step 4: Run** `pytest tests/test_excluded.py tests/test_mentions.py tests/test_search.py -v` — expect PASS.
- [ ] **Step 5: Commit** `git commit -m "Keep identity documents out of genea tool results"`

### Task 2: Revert updated records when a proposal fails

**Files:**
- Modify: `src/genea/gramps/write.py` (`_Writer.update`, `_Writer.clean_up`)
- Test: `tests/test_gramps_write.py`

**Interfaces:**
- Produces: `_Writer.update` keeps `self.originals: list[tuple[str, dict]]` (kind, object as read before the PUT). `clean_up()` first deletes created objects newest first (as today), then for each original newest first GETs the current object for a fresh ETag and PUTs the original back. The message suffix becomes `" (cleaned up)"` when everything was reverted, and names `"<singular> <id> could not be restored; check it in Gramps"` only for failed restores.
- Note: `apply._write_all` matches the substring `"already updated"` to mark a proposal failed; change that match to `"could not be restored"`.

- [ ] **Step 1: Write failing tests** — `test_failed_proposal_restores_updated_person` (make the second request of `add_event` fail in the fake server after the event POST and person PUT; assert the person's `event_ref_list` equals the original and the event is deleted); `test_restore_failure_named` (the restoring PUT also fails → message contains `could not be restored`).
- [ ] **Step 2: Run** `pytest tests/test_gramps_write.py -k restore -v` — expect FAIL.
- [ ] **Step 3: Implement** as above; the restore PUT goes through `self.client.update` and is logged as `"PUT"` with the proposal id.
- [ ] **Step 4: Run** `pytest tests/test_gramps_write.py tests/test_analysis_apply.py -v` — expect PASS.
- [ ] **Step 5: Commit** `git commit -m "Restore updated Gramps records when a proposal fails"`

### Task 3: Source line, event notes and family owners in proposals

**Files:**
- Create: `src/genea/analysis/sourcenote.py`
- Modify: `src/genea/changes/store.py` (`GEDCOM_REQUIRED`), `src/genea/analysis/api.py` (`_check_ids`, `MULTILINE`), `src/genea/analysis/iteration.py` (`add_event`, `add_note`), `src/genea/gramps/resolve.py` (`_add_event`, `_add_note`), `src/genea/gramps/write.py` (`_add_event`, `_add_note`), `src/genea/mcp_server.py` (propose docstring)
- Test: `tests/test_sourcenote.py`, `tests/test_analysis_proposals.py`, `tests/test_analysis_iteration.py`, `tests/test_gramps_write.py`, `tests/test_gramps_resolve.py`

**Interfaces:**
- Produces: `with_source(text: str, sources: list[SourceRef]) -> str` returns `text.rstrip() + "\n\nSource: " + "; ".join(s.cite() for s in sources)`; `same_note(a: str, b: str) -> bool` compares with whitespace runs collapsed.
- Payload changes: `add_event` and `add_note` take `person_id` **or** `family_id` (exactly one; `GEDCOM_REQUIRED` keeps only `type` / `text`, `_check_ids` enforces one owner). `add_event` takes optional `note` (text) and `description`. Family events accept GEDCOM family tags `MARR`, `DIV`, `ENGA`, `MARB`, `MARS` mapped in `TAG_TO_GRAMPS` to `Marriage`, `Divorce`, `Engagement`, `Marriage Banns`, `Marriage Settlement`.
- Every note written by `add_note`, and the note attached to a new event, is `with_source(text, proposal.sources)`. The attached event note is created in Gramps and placed in the event's `note_list` before the event is POSTed; in the GED iteration it is a level-2 `NOTE` under the event.
- Resolver: `_add_note` / `_add_event` raise `Stale("already in Gramps: ...")` when an existing note on the owner (or on a same-type, same-date event) satisfies `same_note` with the composed text. This is the re-run check.

- [ ] **Step 1: Write failing tests** — `test_with_source_appends_cites`; `test_same_note_ignores_spacing`; `test_propose_note_on_family`; `test_propose_needs_one_owner` (both or neither → `ToolError`); `test_iteration_family_marriage` (level-1 `MARR` under the FAM record with a level-2 NOTE ending in `Source: [...]`); `test_write_event_with_note` (fake Gramps holds a Note whose text ends with `Source:` and the event's `note_list` names it); `test_resolve_rerun_note_stale`.
- [ ] **Step 2: Run** the listed test files with `-k "source or family or owner or rerun or event_with_note"` — expect FAIL.
- [ ] **Step 3: Implement** as above. Family owner reads go through `Resolver._read("families", family_id)`; the family PUT appends to `event_ref_list` (role `Family`) or `note_list`.
- [ ] **Step 4: Run** `pytest tests -q` — expect PASS.
- [ ] **Step 5: Commit** `git commit -m "Write notes with their source and allow family events and notes"`

### Task 4: Residences and new places

**Files:**
- Modify: `src/genea/analysis/api.py` (`_check_ids`), `src/genea/analysis/iteration.py` (`add_event`), `src/genea/gramps/resolve.py` (`Target`, `resolve`, `place_handle`), `src/genea/gramps/write.py` (`_add_event`, `CLASSES`, `SINGULAR`)
- Test: `tests/test_analysis_proposals.py`, `tests/test_gramps_resolve.py`, `tests/test_gramps_write.py`

**Interfaces:**
- Payload: `add_event` with `type: "RESI"` takes `address` (required for RESI, one line); the event description is `f"Address: {address}"` in both GED (the event value: `1 RESI Address: <address>`, no `ADDR` structure) and Gramps (`description`). `date` stays optional.
- `Target.new_place: str | None` — set by `resolve` when `place` matches no Gramps place title; when it matches more than one, the existing `ValueError` stands.
- Writer: `_add_event` creates `{"_class": "Place", "name": {"_class": "PlaceName", "value": new_place}, "title": new_place}` through `w.create("places", ...)` (tracked, so Task 2's clean-up deletes it) before the event. Add `"places": "Place"` / `"place"` to `CLASSES` / `SINGULAR`. The Resolver's cached `places` list is invalidated after a create so the next proposal of the same run finds it.

- [ ] **Step 1: Write failing tests** — `test_resi_needs_address`; `test_iteration_resi_description` (`1 RESI Address: Hlavná 5, Nitra`); `test_write_resi_new_place` (fake has no place → one Place POSTed, event's `place` is its handle, description `Address: ...`); `test_second_resi_same_place_reuses` (two proposals, one place created); `test_ambiguous_place_not_created`.
- [ ] **Step 2: Run** with `-k "resi or place"` — expect FAIL.
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run** `pytest tests -q` — expect PASS.
- [ ] **Step 5: Commit** `git commit -m "Write residences with their address and create missing places"`

### Task 5: Conflict decisions

**Files:**
- Modify: `src/genea/analysis/models.py` (`Proposal`), `src/genea/analysis/api.py` (`propose`), `src/genea/analysis/iteration.py`, `src/genea/gramps/resolve.py`, `src/genea/gramps/write.py`, `src/genea/analysis/apply.py` (`prepare`), `src/genea/mcp_server.py` (`propose` parameter)
- Test: `tests/test_analysis_proposals.py`, `tests/test_analysis_iteration.py`, `tests/test_gramps_write.py`, `tests/test_analysis_apply.py`

**Interfaces:**
- `Proposal.conflict: Conflict | None` with `class Conflict(BaseModel): line: int; existing: str; decision: Literal["undecided", "keep", "replace", "both"] = "undecided"`. `existing` is a one-line description of the tree's value; `line` is the GEDCOM line of the existing event (anchored like `update_event` via `_anchor`, stored in `Proposal.anchor`).
- Only `add_event` and `add_note` may carry a conflict. `propose(..., conflict_line: int | None = None)` sets it.
- Semantics: `keep` → the proposal is treated as withdrawn by build and apply; `both` → written as a normal add; `replace` → written, then a note `with_source(f"Superseded by {new value} (genea, {proposal id}).", sources)` is added to the anchored old event (GED: level-2 NOTE under the old event; Gramps: Note appended to the old event's `note_list`). The old event is otherwise untouched.
- `prepare` raises `ToolError("<ids> have an undecided conflict; decide keep, replace or both in the review PR.")` while any active proposal's decision is `undecided`.

- [ ] **Step 1: Write failing tests** — `test_propose_conflict_anchored`; `test_build_keep_skips`; `test_build_replace_marks_old` (old BIRT keeps its DATE and SOUR, gains a NOTE starting `Superseded by`); `test_write_replace_marks_old`; `test_prepare_refuses_undecided`.
- [ ] **Step 2: Run** with `-k conflict or keep or replace or undecided` — expect FAIL.
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run** `pytest tests -q` — expect PASS.
- [ ] **Step 5: Commit** `git commit -m "Record conflict decisions and mark replaced values"`

### Task 6: Review PR and apply from it

**Files:**
- Create: `src/genea/analysis/bundle.py`
- Modify: `src/genea/analysis/models.py` (`Analysis.review`), `src/genea/gramps/snapshot.py`, `src/genea/analysis/apply.py`, `src/genea/cli/grampscmds.py`
- Test: `tests/test_bundle.py`, `tests/test_gramps_snapshot.py`, `tests/test_analysis_apply.py`, `tests/test_cli_gramps.py`

**Interfaces:**
- `Analysis.review: Review | None`, `class Review(BaseModel): branch: str; pr: str; file: str` (repo-relative path `genea-review/<analysis name>.md`).
- `render_bundle(analysis: Analysis) -> str`: a Markdown file with an intro (how to drop an item: delete its section and commit; how to decide: edit the `decision:` line), then sections grouped **Events**, **Residences**, **Notes**, **Conflicts**, **Leads** (a note whose text starts `Leads:`). Each item is `### <proposal id>: <describe_operation(...)>` followed by the full text to be written, its sources, and for conflicts `Existing: <existing>` and `decision: undecided`.
- `parse_bundle(text: str, analysis: Analysis) -> tuple[set[str], dict[str, str]]`: kept proposal ids and decisions. Raises `ToolError` naming the line for an unknown id, a duplicated id, or a decision word outside keep/replace/both/undecided. Only ids and decisions are read; any other edit is ignored and reported back in the apply message as "edits other than deleting sections and decisions are not applied".
- `snapshot.open_review(repo, ged_rel, name, bundle_rel, bundle_text, diff_text, title, body, run) -> tuple[str, str]` (branch, PR url): branch `genea/<today>-<name>-review` (suffix `-2`… if taken), commits the bundle file and `genea-review/<name>.diff`, pushes, opens the PR, checks out main again; on error the same rollback as `open_pr`.
- `snapshot.push_snapshots(repo, ged_rel, branch, before, after, title, run) -> None`: checks out `branch` (after `git fetch origin` and fast-forward to `origin/<branch>`), writes the before commit (only if different from main) and the after commit as `open_pr` does, pushes, checks out main.
- `apply.open_review_pr(ctx, name, run=default_runner) -> str` (builds the iteration first, saves `analysis.review`); `apply.read_review(ctx, name, run=default_runner) -> None` (reads the bundle from `origin/<branch>` via `git show`, withdraws dropped proposals, sets decisions, rebuilds the iteration). `apply_analysis` uses `push_snapshots` onto `analysis.review.branch` instead of `open_pr` when `analysis.review` is set, and the PR url stays `analysis.review.pr`.
- CLI: `genea analysis review NAME` (opens the review PR, prints its url) and `genea analysis apply NAME` (when the analysis has a review: `read_review` first, then the usual preview and confirm).

- [ ] **Step 1: Write failing tests** — `test_render_parse_roundtrip`; `test_parse_dropped_section`; `test_parse_bad_decision_names_line`; `test_parse_unknown_id`; `test_open_review_commits_bundle` (fake runner records `git` calls: checkout -b, add, commit, push, `gh pr create`, checkout main); `test_push_snapshots_on_review_branch`; `test_apply_from_review_withdraws_dropped` (one section removed → that proposal `withdrawn`, others written, no new PR opened).
- [ ] **Step 2: Run** `pytest tests/test_bundle.py tests/test_gramps_snapshot.py tests/test_analysis_apply.py -v` — expect FAIL.
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run** `pytest tests -q` — expect PASS.
- [ ] **Step 5: Commit** `git commit -m "Review a bundle as a PR before writing it to Gramps"`

### Task 7: The collect-person skill

**Files:**
- Create: `.claude/skills/collect-person/SKILL.md`
- Modify: `README.md` (one section: how to run it)

**Interfaces:**
- Consumes: MCP tools `find_person`, `get_person`, `relatives`, `timeline`, `documents_about` (all pages), `read_document`, `find_conflicts`, `analyze`, `propose` (with `family_id`, `note`, `address`, `conflict_line`), `build_iteration`; CLI `genea analysis review NAME`.

- [ ] **Step 1: Write the skill** in the style of `.claude/skills/summarize-transcripts/SKILL.md`. It covers:
  - **Input:** a person or a family. A family runs `analyze family`, which holds the family facts, plus one `analyze person` per spouse and child. That makes one review PR per member and one for the family, and the family PR is merged last.
  - **Gathering:** read every page of `documents_about`, and read each document in full.
  - **Comparing:** check each fact against `get_person` and `timeline`. Already present → skip. New → propose. Different → propose with `conflict_line`.
  - **Mapping:**
    - addresses → RESI with `address`
    - births, deaths, baptisms, marriages and similar → events with a `note`
    - everything else → `add_note`
  - **Other people:** never proposed for. They are collected into one `add_note` on the chosen person, starting `Leads:`, with one line per person. A family member who is in the same run gets the fact in their own bundle instead.
  - **Finish:** run `genea analysis review NAME` and report the PR link in one or two lines (see the brief-report preference).
- [ ] **Step 2: Check the skill by hand** against one person in a scratch analysis, with the genea MCP connected. The analysis has a review PR whose bundle lists events, residences, notes, conflicts and leads, and nothing has been written to Gramps yet.
- [ ] **Step 3: Commit** `git commit -m "Add the collect-person skill"`
