# Person / family information collection into Gramps — design

## Intent

Today the analyze/propose flow writes changes to Gramps Web one proposal at a
time. The user wants a deeper, person-centred pass: pick a person or family,
let Genea gather everything it holds on them (documents, transcripts, existing
conflicts), compare that with what the Gramps tree already says, and add only
what is new, with sources. The user reviews one bundle per person and approves
it whole.

## Goals

- Invoke with a person or a family; collect all available information in depth.
- Compare with the tree's existing events, notes and citations; add only what
  is new. A re-run on the same person skips anything already written.
- Mapping into the tree:
  - Addresses become Residence events, dated when a date is known, with the
    value `Address: <Address>`.
  - Births, deaths, baptisms, marriages and similar become Events.
  - Everything else becomes Notes, each with its source included.
  - Family-level facts (marriage, family notes) go on the family object; each
    family member also gets the person process.
- Every written item carries a citation to its source document.
- Conflicts with existing tree values are flagged next to the existing value.
  The user chooses keep, replace, or keep both. A bundle cannot be approved
  while any conflict is undecided.
- Information about other people found in the documents is never written for
  them. It is listed as leads in the review bundle and saved as a note on the
  chosen person.
- Review is one bundle per person, approved whole. Before/after snapshot PRs
  open as in the existing write-back.

## Non-goals

- Writing data for people other than the chosen one (they are leads only).
- Automatic code-based extraction of events from documents.
- Auto-resolving conflicts.

## Anti-goals

- Duplicate events or notes after a repeat run.
- A half-applied bundle that leaves the tree pointing at deleted or
  partly updated data.
- A conflict silently overwritten.

## Constraints

- Claude does the reading and judging in the user's session (as `propose`
  does today), driving the existing Genea tools. It is delivered as a skill
  (like the extract and summarize skills) plus small code additions. This
  was the user's choice.
- Builds on the existing pipeline: `documents_about`, `get_person`,
  `timeline`, `find_conflicts`, `propose`, `build_iteration`, apply and the
  snapshot PRs. Person ids are `tree.ged#@I12@`.
- Excluded document tags (travel card, resident permit, id card, driving
  license, health insurance card) must never be used or uploaded.

## Known gaps in the code (from a survey, not yet verified by running anything)

- No orchestrator that collects, extracts, diffs and produces proposals.
- No leads list.
- No Residence-specific writer; whether the event-type mapping covers RESI
  is unchecked.
- `add_source_citation` attaches to the owner, not to a newly created event.
- `write.py` clean-up deletes only objects created for the failed proposal;
  already-updated existing records are not reverted. With many items per
  bundle this must be fixed.

## Decisions by the user

- Conflicts are flagged, user decides.
- Address/event/note mapping as above.
- Only the chosen person's own data is written; others are leads.
- Whole-person bundle approval.
- Claude in a session does the judging.
- Family: both family-level records and per-member person process (the user
  answered "yes" to an either/or question, so this is the reading to check).
- Leads are saved as a note on the person as well as shown in the bundle.
- Dropping items: the bundle is reviewed as a PR. The user drops items by
  editing the PR (another commit on its branch). What remains in the PR is
  what gets written. (Author's reading: the proposals, not only the GED
  snapshots, live in the PR in an editable form, and apply reads the PR's
  final state.)
- "Replace" in a conflict: the new value is added and the old one is marked,
  never deleted or edited in place. The old record keeps its citations.
- Missing place or source: Genea creates the new Residence place or the
  document's source in the tree. This is new; today the write-back only
  resolves places and sources by title and never creates them.

## Calls made by the author (to check)

- Re-runs skip already-written items.
- Conflicts are the one exception to whole-bundle approval.
- Delivery as a skill plus code additions.

## Further calls by the author (from the builder check; to check)

- The comparison with the tree is Claude's judgement in the skill. Code only
  validates ids, checks "already written" and writes.
- The bundle reuses the existing `propose` / `build_iteration` / apply
  format, extended with a conflict-decision field. No new apply path.
- "Already written" is recognised by a citation to the same source document
  (file + page) on the same person or event, plus comparison with tree
  content. No tag is added to Gramps records.
- A note carries its source as text inside the note (Gramps cannot attach a
  citation to a note) and a citation on the owning person or family. A new
  event carries its own citation (this fixes the `add_source_citation` gap).
- Leads: each family member gets their own lead note. A fact about a family
  member that is also being processed is written in that member's own run,
  not duplicated as a lead.
- Excluded document tags are filtered in code (`documents_about` and
  friends), not only by instruction in the skill.
- A family run produces one bundle (PR) per member plus one family bundle;
  the family bundle is approved last.
- Creating a place or source first looks for an existing one by title, so
  re-runs don't create duplicates.

## Also left to the builder

- How an old value is "marked" after a replace (for example a note or an
  attribute on the old event), as long as it is visible in Gramps and the old
  record and its citations are otherwise untouched.
- The editable proposal file format inside the PR.

## Left to the builder

- Matching rule for "already in the tree" (dates, places, wording tolerance).
- Note and citation text format, and Residence date-less handling.
- Bundle presentation format.
- How the clean-up fix reverts updated records (snapshot/restore or inverse
  operations).
- Tests and fixtures (extend `fake_gramps.py`).
