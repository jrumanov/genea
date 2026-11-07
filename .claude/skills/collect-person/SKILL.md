---
name: collect-person
description: Use when the user asks to collect, gather or add everything genea holds about one person or one family into the Gramps tree, or to prepare a review PR with new events, residences and notes for a person.
---

# Collect a person's information into Gramps

You read what the knowledge base holds on one person (or one family), compare it with what the tree
already says, and propose only what is new. The user reviews one PR per person and approves it whole.
Nothing reaches Gramps until the user runs `genea analysis apply`; you only propose and open the review.

Use the genea MCP tools. Person ids look like `tree.ged#@I12@`, family ids like `tree.ged#@F3@`. Run the CLI
from this repo with `uv run genea --kb "<kb>"`; the user's knowledge base is `D:\Foto DB` (never a subfolder).

Documents tagged `travel card`, `resident permit`, `id card`, `driving license` or `health insurance card` are
filtered out by the tools. If one still turns up, do not use it and do not mention its content.

## 1. Input

A person or a family. Find the id with `find_person` if the user gave a name; ask which one only if several
people fit.

- **Person:** one run, `analyze` with scope `person`.
- **Family:** `analyze` with scope `family` holds the family facts (marriage, family notes); then one
  `analyze` with scope `person` per spouse and per child. That gives one review PR per member and one for the
  family. Tell the user the family PR should be merged last.

## 2. Gather

For the chosen person: `get_person`, `timeline`, `relatives`, `find_conflicts`, and `documents_about` for
**every page** (`page` 1 to `pages`). Then `read_document` for each listed file, in full (all pages of a PDF,
all parts of a long transcript). A snippet is not enough to write a note from.

Files marked `ambiguous` may be about a namesake: use a fact from them only when the text itself fits this
person (dates, parents, place).

## 3. Compare

Check every fact against `get_person` and `timeline` before proposing it.

- Already in the tree (same event, date and place, or the same statement in a note) → skip it.
- Not in the tree → propose it.
- Different from the tree's value (another date or place for an event the tree has) → propose it with
  `conflict_line`, the line of the tree's event (`get_person` lists it). Never decide the conflict yourself:
  the user chooses keep, replace or both in the review.

Cite every proposal with the sources the tools returned, like `[docs/matrika.pdf p.3]`, and give a rationale
that says what the document says. Sources are text at the end of a note; genea adds them, so do not write a
`Source:` line yourself.

## 4. Map

`propose` on the analysis of the chosen person:

| What the document gives | Proposal |
|---|---|
| An address | `add_event` type `RESI` with `address` (one line), `date` if known, `place` if known |
| Birth, baptism, death, burial, marriage and similar | `add_event` with the right tag, `date`, `place`, and a `note` with the wording of the document |
| Family facts (marriage, divorce) | `add_event` with `family_id` (MARR, DIV, ENGA, MARB, MARS) in the family's analysis |
| Everything else (occupation details, character, stories, illness, relations) | `add_note` |

Dates in GEDCOM form (`12 MAR 1880`, `ABT 1855`). A place genea does not know yet is created in Gramps; use
the town name as the documents write it, one spelling throughout.

## 5. Other people

Never propose events or notes for anyone but the chosen person. Facts about someone else go into **one**
`add_note` on the chosen person whose text starts `Leads:`, with one line per person (name, the fact, the
file). The same notes appear as the Leads section of the review. Exception: a family member who is in the same
run gets the fact in their own run instead of a lead.

## 6. Finish

Run `uv run genea --kb "<kb>" analysis review <analysis name>` (the name is in the `analyze` result). It
builds the iteration and opens the PR. Report in one or two lines: the PR link and how many events,
residences, notes and conflicts it holds. No per-document summaries, and do not ask about commits.

The user then edits the PR (deletes a section to drop an item, sets `decision:` on each conflict) and runs
`genea analysis apply <name>` themself. A re-run on the same person skips what is already in Gramps, so it is
safe to collect again after new documents arrive: run `analyze` for the person again first (it starts a new
review), then repeat steps 2 to 6. An analysis that already has a review PR cannot open a second one.
