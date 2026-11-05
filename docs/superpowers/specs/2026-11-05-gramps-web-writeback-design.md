# Write approved proposals to Gramps Web, snapshot the tree by PR — Design

Date: 2026-11-05
Status: Draft, awaiting review. Replaces the "phase two: promote" section of
`2026-10-11-analyze-propose-changes-design.md` (§8) and the write-back workflow brainstormed on 2026-10-09.

## 1. Intent

`genea analyze` finds problems in the tree, Claude or the user records proposals with citations, and `genea
analysis build` makes a marked copy of the tree under `.genea/analyses/`. Today the accepted corrections still
end there: somebody has to type them into Gramps by hand.

This feature closes the loop. After the user has reviewed an analysis' diff, genea writes its proposals
**into Gramps itself**, through the Gramps Web API, and then records the new state of the whole tree as a PR on
`fam_tree_ged`, the same way the user's own Gramps exports are recorded today.

Why this shape (decided with the user):

- **Gramps is the only master.** Gramps' GEDCOM import never merges (it duplicates people), so a change made
  to the `.ged` alone is lost at the next Gramps export. Writing into Gramps means a genea change can never
  become "detached" from the master.
- **The user edits only in desktop Gramps** and cannot edit the `.ged` by hand. Desktop Gramps and Gramps Web
  are kept in step by the Gramps Web Sync add-on, so the user does no manual Gramps entry and no verify step.
- **Gramps Web runs on the user's Raspberry Pi and is reachable from the internet.**
- **git keeps the state of the tree.** `latest_jrumanovsky.ged` in `github.com/jrumanov/fam_tree_ged` (clone at
  `D:\Foto DB\rodinna historia\fam_tree_ged`) stays the snapshot history and genea's indexed file. Snapshots
  are added **by PR, like the current "Update <date>" exports**.

```
                  approve diff
genea analysis ──────────────▶ Gramps Web (Pi) ◀── Sync add-on ──▶ desktop Gramps (user's manual edits)
                                     │
                         GEDCOM (+ Gramps XML) export
                                     ▼
                     fam_tree_ged: branch + PR "genea <date> <analysis>"
                                     ▼  (user merges, pulls)
                              genea index reads latest_jrumanovsky.ged
```

## 2. Goals, non-goals, anti-goals

Goals
- Write the active proposals of one analysis to Gramps Web with one command, after a preview.
- Never overwrite something the user changed in Gramps after the proposal was made: detect it, write nothing
  for that proposal, mark it `stale`, and show both values.
- Every write is undoable and logged.
- After a write, a PR on `fam_tree_ged` with the full new export, whose commit message lists each written
  proposal with its rationale and citations.

Non-goals
- Editing the `.ged` file directly (genea and the user never do).
- A Gramps import of any genea file.
- Writing from the local pydantic agent or from MCP (see §7).
- New operations beyond the six existing ones (`add_event`, `update_event`, `add_note`, `add_source_citation`,
  `link_child`, `add_person`).
- Deleting anything in Gramps that genea did not create. genea deletes only objects it created itself: during
  the clean-up of a failed proposal (§6) and when undoing an `add_*` proposal (§8).
- Running or configuring Gramps Web, the Pi or the Sync add-on.

Anti-goals (a failure even if it technically works)
- A write that silently replaces a value the user edited in Gramps.
- Duplicate people, events, places or sources created in Gramps.
- `GENEA ...` marker notes or other genea scaffolding ending up in the user's real tree.
- Credentials or tokens in a file under git (the KB config, `fam_tree_ged`, this repo).
- A half-written proposal left in Gramps (e.g. a new event that no person references) without the user being
  told and the log saying how to clean it up.

## 3. The flow

`genea analysis apply <name> [--proposal <id>…] [--dry-run]`

1. **Preconditions.** The analysis has a build (`vN`) whose proposals equal the current active proposals and
   whose base hash equals the current indexed `.ged`; otherwise genea says to run `analysis build` first. The
   `fam_tree_ged` clone is clean and on `main`, and `main` is not behind `origin/main` (genea fetches first).
2. **Preview.** genea prints, per proposal, the operation in words (`describe_operation`), the diff hunk from
   `vN.diff`, the citations, and the Gramps object it will touch. `--dry-run` stops here. Otherwise the user
   confirms (`typer.confirm`, `--yes` to skip).
3. **Resolve.** For each proposal, map tree ids to Gramps handles (§4) and read the current Gramps objects.
4. **Check.** Compare the current Gramps state with what the proposal was made against (§5). A mismatch marks
   the proposal `stale` with both values; the others go on.
5. **Write.** Each proposal is written with the object endpoints (§6), guarded by `If-Match` ETags. The
   transaction each request returns is appended to the write log (§8). A proposal that is written becomes
   `written` (new proposal status), with the Gramps handles it touched.
   Before the first write, genea downloads the exports (§9) as the **before** snapshot: the tree as it is in
   Gramps Web, which may already hold the user's synced desktop edits that no export has recorded yet.
6. **Snapshot.** If anything was written: download the exports again as the **after** snapshot and open one PR
   on `fam_tree_ged` with two commits, before then after, so the genea commit holds only genea's changes (§9).
7. **Report.** Counts (written / stale / failed), the PR URL, and what to do next: merge the PR, `git pull`,
   `genea index`.

`genea analysis undo <name> <proposal-id>` undoes one written proposal through the Gramps Web undo endpoint
(§8) and sets it back to `active`. It also makes a snapshot PR.

`genea snapshot` makes only the snapshot PR (step 6), for the user's own use; its commit message says it holds
no genea writes.

## 4. Mapping genea ids to Gramps objects

The `.ged` is a Gramps export, and its xrefs are the Gramps ids (`@I0000@` is Gramps id `I0000`). genea ids like
`latest_jrumanovsky.ged#@I0012@` map to Gramps objects by `gramps_id` (`GET /api/people/?gramps_id=I0012`, same
for families). Only a `.ged` that is mapped to a Gramps Web tree in the config (§10) can be written; a
proposal for any other `.ged` is refused. At first only `latest_jrumanovsky.ged` is mapped.

Events inside a person are identified the way proposals already anchor them (event type plus the raw date and
place in the `.ged`), matched against the person's event references in Gramps.

Places and sources are matched by exact title. If no Gramps place or source matches exactly, the proposal is
not written and is reported as "place/source not in Gramps; add it there or give its Gramps id". genea never
creates places or sources (anti-goal: duplicates). A payload may name a place or source by Gramps id instead
of title. (My call: the alternative, fuzzy matching or creating places, risks duplicates that are painful to
merge in Gramps.)

New people get Gramps ids from Gramps (genea sends none), never `@GENEA<n>@`.

## 5. Not overwriting the user's edits

A proposal was made against one export of the tree. Before writing, genea checks that the parts it changes
are still as they were in that export:

- `update_event`: the event's date, place and description in Gramps equal the proposal's `anchor`.
- `link_child`: the child is not already in the family, and has no other parent family that the proposal did
  not know about.
- `add_event`, `add_note`, `add_source_citation`: the person still exists; for `add_event`, the person has no
  event of the same type with an equal date (that would be a duplicate).
- `add_person`: no person in Gramps has the same given name, surname and birth date (a duplicate check, not a
  guarantee).

Then every `PUT` sends the object's ETag (`If-Match`) from the read in step 3, so an edit that lands between
the read and the write gets HTTP 412, and the proposal is marked `stale` instead.

Edits that exist only in an unsynced desktop Gramps are invisible to genea. If one touches the same object,
the Sync add-on asks the user at the next sync which side to keep. If the user keeps the desktop version,
the genea change disappears from Gramps; the next `analyze`/`build` run on the new export shows the
finding again, and the write log (§8) says the proposal was written once. The `analysis show` output lists
`written` proposals whose change is no longer in the indexed `.ged` as "written, not in the current tree".

## 6. Writing each operation

Only the object endpoints (`POST /api/<objects>/`, `PUT /api/<objects>/<handle>`) are used; they maintain
references and search indices. The raw `POST /api/transactions/` endpoint is not used (its own docs: objects
are written unvalidated and references are not maintained).

| Operation | Requests |
|---|---|
| `update_event` | `PUT /events/<h>` with the new date/place/description |
| `add_event` | `POST /events/`, then `PUT /people/<h>` adding the event reference (birth/death reference index set for BIRT/DEAT if the person has none) |
| `add_note` | `POST /notes/`, then `PUT /people/<h>` adding it to the note list |
| `add_source_citation` | `POST /citations/` (source handle, page), then `PUT` the person or the named event adding it to the citation list |
| `link_child` | `PUT /families/<h>` adding the child reference (Gramps maintains the person's parent family list; the builder verifies this against a live server) |
| `add_person` | `POST /people/` with name, gender, and a birth event created first like `add_event` |

Dates: GEDCOM date strings in payloads are turned into Gramps date objects (exact, about, before, after,
between, range; Julian/Gregorian as the `.ged` mapper already reads them). A date genea cannot represent
exactly refuses the proposal; it is never rounded.

A proposal that needs several requests is written in order "create new objects, then update the object that
refers to them". If a later request fails, genea deletes the objects it created for that proposal, logs it,
and marks the proposal `failed` (new status, kept with the error; the user can retry). If even the clean-up
fails, the report names the orphan objects by Gramps id.

genea writes no marker notes into Gramps. Provenance lives in the write log and the PR's commit message.
(My call: notes in the user's real tree would be clutter in every Gramps view.) The rationale and citations do
not go into Gramps either, unless the proposal itself is an `add_note` or `add_source_citation`.

## 7. Who may write

Only the CLI writes to Gramps, and only after the user confirms the preview. MCP keeps its current tools:
Claude can propose, withdraw and build, but there is no MCP tool for `apply` or `undo`, and the server
instructions keep saying Claude never changes the family tree. (My call, following the earlier decision that
dismissing is the user's decision, not Claude's: writing to the master tree is a bigger step.)

The dead `gedcom_direct_write` setting is removed.

## 8. Undo and the write log

`.genea/analyses/<name>/writes.jsonl`, append-only, one line per request: time, proposal id, method, path,
Gramps handle and id, HTTP status, the changes returned by Gramps Web, and the id of the transaction in
`/transactions/history/`.

The write responses do not carry the transaction id. genea tags every write with a commit message
`genea <analysis> <proposal-id>`, then reads `/transactions/history/` with an `after_id` cursor (the newest id
seen before writing) and records the ids of the entries carrying its message. This needs a Gramps Web API
release from August 2026 or later (undo endpoint, commit messages, `after_id`); `genea gramps check` and
`apply` refuse an older server.

`analysis undo` calls `POST /api/transactions/history/<id>/undo` for the proposal's transactions, newest first.
If Gramps refuses (the object changed since), genea reports it and leaves the proposal `written`; the user
fixes it in Gramps. The PR history in `fam_tree_ged` is the second safety net: every snapshot is a full tree.

## 9. Snapshot PR

- Exports: `GET /api/exporters/ged/file` and `GET /api/exporters/gramps/file`, full tree, no privacy filter,
  living people included (the repo is private; the user's own exports also include everyone). The builder
  checks that the GEDCOM export from Gramps Web matches what desktop Gramps writes (header, `CHAR UTF-8`,
  xrefs = Gramps ids) closely enough that the diff against the last desktop export is only the real changes.
- The `.ged` replaces `latest_jrumanovsky.ged`; the XML goes next to it as `latest_jrumanovsky.gramps`, as a
  full-fidelity backup (GEDCOM drops some Gramps data). genea's indexer ignores `.gramps` files. (My call.)
- Branch `genea/<YYYY-MM-DD>-<analysis-name>` with two commits (decided with the user):
  1. **Before**: the exports taken just before the first write, message `Snapshot <date> before genea`. If
     they equal `main`, this commit is skipped. If not, it holds the user's synced edits that no export
     recorded, so they are never attributed to genea.
  2. **After**: the exports after the writes, title `genea: <analysis> (<n> changes)`, then per written
     proposal the description, rationale and citations; stale and failed ones listed as not written.
  Push, `gh pr create` with the genea commit's text (plus a line saying whether a before-snapshot is
  included), then return the clone to `main`. With squash merges, the PR still lands as one commit; the
  two commits are for review.
- Edits that sync to Gramps Web *during* the writes would land in the after commit. Writes take seconds, so
  this is accepted and not detected.
- genea does not merge. Unlike the earlier design, the PR is **not** the approval: the change is already in
  Gramps when the PR opens; the PR records it. Closing it unmerged does not undo anything (use `analysis
  undo`). The next snapshot (genea's or the user's own export) includes the change anyway.
- After the user merges and pulls, `genea index` picks up the new tree as for any export.

## 10. Connection and credentials

Gramps Web is on the internet, so:

- KB config `gramps_web_url` (one host; the user provides it once the Pi is running). `https://` is
  required, `http://` is refused except for `localhost`. TLS certificates are verified.
- Plain Gramps Web username/password login (decided with the user: no proxy login or OIDC in front).
- The server runs in multi-tree mode; every tree has its own users, so trees stay fully separated on one host
  (decided with the user). The KB config maps each writable `.ged` to a user, e.g.
  `[gramps_web.trees] "latest_jrumanovsky.ged" = "genea-jrumanovsky"`. genea always logs in with the user of
  the `.ged` it writes, and records the tree id from the token on first login. A later login that reaches a
  different tree is refused.
- Passwords come from the Windows Credential Manager (`keyring`, one entry per user) or an environment
  variable `GENEA_GRAMPS_PASSWORD_<USER>`, never from a file. `genea gramps login <ged>` stores one after a
  test login.
- Login with `POST /api/token/`; the access and refresh tokens stay in memory for one command.
- The spec recommends one dedicated user per tree with the Editor role: add, edit, and delete for the clean-up
  and undo in §6 and §8; not owner/admin. The owner account also works. `genea gramps check [<ged>]` reports
  the user's tree and permissions and the server's API version.
- The snapshot PR goes to the git repo that holds the mapped `.ged` (for `latest_jrumanovsky.ged`,
  `fam_tree_ged`).
- Requests time out; a connection failure before any write changes nothing; one during a proposal is handled
  like any failed request (§6).

## 11. What is left to the builder

- The HTTP client (`httpx` is already a dependency through the Ollama client; check) and its module layout
  (suggested `src/genea/gramps/` for the client and the operation writers, CLI in `cli/analysiscmds.py`).
- The exact Gramps JSON shapes for events, dates, references and the person's birth/death reference index,
  taken from the Gramps Web API spec for the running server version.
- Tests: unit tests against a fake API built from recorded responses (each operation, 412 handling, failure
  clean-up, stale checks, id mapping, refusal of other `.ged` files and of `http://`); snapshot tests with a
  temporary git repo and a faked `gh`; an optional integration test (marked, skipped by default) against a
  throwaway Gramps Web container. A manual live check against the Pi before merging.
- How the preview is laid out in the terminal.

## 12. Open points

- The Gramps Web URL (the user provides it once the Pi is running); the live check waits for it.
- Whether `.gramps` exports in `fam_tree_ged` are welcome (the user's own exports add only the `.ged` today);
  a KB setting `gramps_web_xml_snapshot` (default on) keeps either choice open.
