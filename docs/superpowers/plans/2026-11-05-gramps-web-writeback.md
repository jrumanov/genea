# Gramps Web Write-back Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `genea analysis apply <name>` writes an analysis' active proposals into Gramps Web after a preview,
never over the user's edits, logs every write for undo, and opens a before/after snapshot PR on the git repo of
the tree.

**Architecture:** A new package `src/genea/gramps/` holds the HTTP client (login, tree pinning, ETag-guarded
object requests, history, undo, exports), a GEDCOM-date → Gramps-date converter, the resolver/stale checks, the
per-operation writers with clean-up and write log, and the git/gh snapshot. `src/genea/analysis/apply.py`
orchestrates them for one analysis; the CLI adds `analysis apply|undo`, `snapshot` and `gramps login|check`.
MCP gets nothing new. All tests run against an in-memory fake Gramps Web on `httpx.MockTransport` and a
temporary git repo with a faked `gh`.

**Tech Stack:** Python 3.12, httpx (already a dependency), keyring (new optional extra `gramps`), typer, pydantic,
pytest. Git and `gh` through `subprocess`.

**Spec:** `docs/superpowers/specs/2026-11-05-gramps-web-writeback-design.md` (read it first; section numbers
below refer to it).

## Global Constraints

- Gramps Web API **3.22.0 or later** (`GET /api/metadata/` → `gramps_webapi.version`); older servers are refused by `gramps check` and `apply`.
- `gramps_web_url` must be `https://`; `http://` only for `localhost`/`127.0.0.1`. TLS verification stays on.
- Passwords only from Windows Credential Manager (`keyring`, service `genea-gramps-web`, username = Gramps Web user) or env `GENEA_GRAMPS_PASSWORD_<USER>` (user upper-cased, non-alphanumerics → `_`). Never from or into any file.
- Only `.ged` files listed in `[gramps_web_trees]` (KB-relative path → Gramps Web user) can be written.
- Only object endpoints (`POST /api/<objects>/`, `PUT /api/<objects>/<handle>` with `If-Match`) write; never `POST /api/transactions/`.
- genea never creates places or sources, never writes marker notes into Gramps, and deletes only objects it created in the same proposal (clean-up) or when undoing.
- No MCP tool for apply/undo; MCP server instructions unchanged.
- Snapshot branch `genea/<YYYY-MM-DD>-<analysis-name>`; before commit `Snapshot <YYYY-MM-DD> before genea`; after commit title `genea: <analysis> (<n> changes)`; the clone is returned to `main`.
- `.gramps` export written next to the `.ged` as `<stem>.gramps` unless `gramps_web_xml_snapshot = false`.
- Remove the unused setting `gedcom_direct_write`.
- Request timeout 30 s.

## Review Focus

1. **A desktop edit synced between preview and write.** Expect: HTTP 412 on the `PUT`, the proposal `stale` with "changed in Gramps while writing", objects it created already deleted. Test: `test_412_marks_stale_and_cleans_up` (Task 5).
2. **An analysis whose tree changed since its build** (user exported, pulled, re-indexed). Expect: `apply` refuses before any request and says to run `analysis build`. Test: `test_apply_refuses_outdated_build` (Task 7).
3. **A `link_child` whose child is a `proposal:p-N` new person** in the same apply. Expect: the person is created first and its new handle is used; if the person proposal goes stale or fails, the link is `stale` too, nothing is written for it. Test: `test_link_child_to_new_person_in_same_apply` (Task 7).
4. **A dirty or behind `fam_tree_ged` clone, or `gh` missing.** Expect: `apply` refuses before writing to Gramps (the snapshot would fail after the writes). Test: `test_apply_refuses_dirty_or_behind_clone` (Task 7).
5. **Wrong tree reached** (the user's credentials changed to another tree's user). Expect: refusal on login naming both tree ids, nothing read or written. Test: `test_login_refuses_other_tree` (Task 2).

---

## File Structure

- Create `src/genea/gramps/__init__.py` (empty).
- Create `src/genea/gramps/credentials.py`: password lookup and storage.
- Create `src/genea/gramps/client.py`: `GrampsClient`, `GrampsError`, `Conflict`, `tree_for`.
- Create `src/genea/gramps/dates.py`: `to_gramps_date`, `same_date`.
- Create `src/genea/gramps/resolve.py`: id → handle mapping, place/source lookup, per-operation stale checks.
- Create `src/genea/gramps/write.py`: per-operation writers, clean-up, write log.
- Create `src/genea/gramps/snapshot.py`: exports into the git repo, before/after commits, PR.
- Create `src/genea/analysis/apply.py`: `apply_analysis`, `undo_proposal`, `snapshot_only`.
- Create `src/genea/cli/grampscmds.py`: `genea gramps login|check`, `genea snapshot`, `genea analysis apply|undo`.
- Modify `src/genea/config.py`, `src/genea/analysis/models.py`, `src/genea/analysis/iteration.py`, `src/genea/analysis/api.py`, `src/genea/cli/app.py`, `src/genea/cli/main.py`, `pyproject.toml`, `README.md`.
- Tests: `tests/fixtures/fake_gramps.py`, `tests/test_gramps_credentials.py`, `tests/test_gramps_client.py`, `tests/test_gramps_dates.py`, `tests/test_gramps_resolve.py`, `tests/test_gramps_write.py`, `tests/test_gramps_snapshot.py`, `tests/test_analysis_apply.py`, `tests/test_cli_gramps.py`.

---

### Task 1: Settings and credentials

**Files:**
- Modify: `src/genea/config.py`, `pyproject.toml`
- Create: `src/genea/gramps/__init__.py`, `src/genea/gramps/credentials.py`
- Test: `tests/test_config.py`, `tests/test_gramps_credentials.py`

**Interfaces:**
- Produces: `Settings.gramps_web_url: str = ""`, `Settings.gramps_web_trees: dict[str, str]` (default empty, via `field(default_factory=dict)`), `Settings.gramps_web_xml_snapshot: bool = True`; `gedcom_direct_write` removed.
- Produces: `credentials.get_password(user: str) -> str` (raises `CredentialsMissing(user)`), `credentials.store_password(user: str, password: str) -> None`, `credentials.env_name(user: str) -> str`, `class CredentialsMissing(Exception)`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py
def test_gramps_web_trees_table(tmp_path):
    (tmp_path / ".genea").mkdir()
    (tmp_path / ".genea" / "config.toml").write_text(
        'gramps_web_url = "https://gw.example.net"\n[gramps_web_trees]\n"a/tree.ged" = "genea-me"\n', encoding="utf-8")
    s = load_settings(tmp_path)
    assert s.gramps_web_url == "https://gw.example.net" and s.gramps_web_trees == {"a/tree.ged": "genea-me"}
    assert s.gramps_web_xml_snapshot is True and not hasattr(s, "gedcom_direct_write")

# tests/test_gramps_credentials.py
def test_env_name():
    assert env_name("genea-jrumanovsky") == "GENEA_GRAMPS_PASSWORD_GENEA_JRUMANOVSKY"

def test_env_wins_over_keyring(monkeypatch):
    monkeypatch.setenv("GENEA_GRAMPS_PASSWORD_ME", "pw")
    assert get_password("me") == "pw"

def test_keyring_used_when_no_env(monkeypatch):  # fake keyring module via monkeypatch.setitem(sys.modules, "keyring", ...)
    assert get_password("me") == "from-keyring"

def test_missing_names_both_ways(monkeypatch):  # no env, keyring returns None or is not installed
    with pytest.raises(CredentialsMissing, match="genea gramps login .*GENEA_GRAMPS_PASSWORD_ME"):
        get_password("me")
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_config.py tests/test_gramps_credentials.py -v`
- [ ] **Step 3: Implement.** `_apply` in `config.py` passes `gramps_web_trees` as `dict(v)`. Remove `gedcom_direct_write` (grep `src`, `tests`, `README.md` for leftovers). `credentials.py` imports `keyring` lazily inside the functions; `ImportError` counts as "no keyring" and the message says `uv sync --extra gramps`. Add extra `gramps = ["keyring>=25"]` to `pyproject.toml` and `keyring>=25` to the dev group.
- [ ] **Step 4: Run, expect PASS**, then the full suite: `uv run pytest`.
- [ ] **Step 5: Commit** `Add Gramps Web settings and password lookup`

---

### Task 2: Gramps Web client and fake server

**Files:**
- Create: `src/genea/gramps/client.py`, `tests/fixtures/fake_gramps.py`
- Test: `tests/test_gramps_client.py`

**Interfaces:**
- Consumes: Task 1 `get_password`, `Settings`.
- Produces in `client.py`:
  - `class GrampsError(Exception)`; `class Conflict(GrampsError)` (HTTP 412); `class TooOld(GrampsError)`; `class WrongTree(GrampsError)`.
  - `MIN_API_VERSION = (3, 22, 0)`.
  - `tree_for(settings: Settings, ged_file: str) -> str` — the Gramps Web user for a KB-relative `.ged`; raises `GrampsError("… is not mapped to a Gramps Web tree; add it under [gramps_web_trees] …")`.
  - `class GrampsClient`: `__init__(self, url: str, user: str, password: str, state_file: Path, transport: httpx.BaseTransport | None = None)`; context manager (closes httpx client).
    - `login() -> str` — `POST /api/token/` `{"username","password"}`; reads the `tree` claim from the access token's JWT payload (base64url decode, no signature check); checks the server version via `GET /api/metadata/` (`TooOld` below `MIN_API_VERSION`); pins the tree in `state_file` (JSON `{user: tree_id}`) on first login, raises `WrongTree` when it differs later. Returns the tree id. Refreshes the access token with `POST /api/token/refresh/` on a 401 once.
    - `info() -> dict` — `{"tree": id, "version": "3.22.0", "permissions": [...]}` from the token claims and metadata.
    - `get(kind: str, handle: str) -> tuple[dict, str]` — object and its ETag (`ETag` header, quotes stripped). `kind` ∈ `people, families, events, notes, citations, places, sources`.
    - `find_by_gramps_id(kind: str, gramps_id: str) -> dict | None` — `GET /api/<kind>/?gramps_id=<id>`.
    - `list_all(kind: str, keys: str) -> list[dict]` — paginated `GET /api/<kind>/?keys=…&page=N&pagesize=500` until fewer than 500 come back.
    - `create(kind: str, obj: dict) -> list[dict]` — `POST`, returns the transaction changes list (201).
    - `update(kind: str, handle: str, obj: dict, etag: str) -> list[dict]` — `PUT` with `If-Match: "<etag>"`; 412 → `Conflict`.
    - `delete(kind: str, handle: str) -> None`.
    - `latest_transaction(obj_class: str, handle: str) -> int` — `GET /api/transactions/history/objects/<Class>/<handle>?sort=-id&pagesize=1`, returns `[0]["id"]`.
    - `undo(transaction_id: int) -> None` — `POST /api/transactions/history/<id>/undo`; a 409/412/422 → `Conflict` with the server's message.
    - `export(extension: str) -> bytes` — `GET /api/exporters/<ext>/file` (ext `ged` or `gramps`).
  - `open_client(kb: KnowledgeBase, ged_file: str, transport=None) -> GrampsClient` — URL check (spec §10), user from `tree_for`, password from `get_password`, `state_file = kb.meta_dir / "gramps_web.json"`; calls `login()`.
- Produces in `tests/fixtures/fake_gramps.py`: `class FakeGramps` with `transport: httpx.MockTransport`, `objects: dict[str, dict[str, dict]]` (kind → handle → obj), `history: list[dict]` (`{"id", "obj_class", "handle", "old", "new"}`), `exports: dict[str, bytes]`, `tree="tree-a"`, `version="3.22.0"`, `fail_on: dict[tuple[str, str], int]` (method+path prefix → status to return once), and `add(kind, obj) -> str` to seed objects. ETag = sha256 of the JSON-dumped object. It records every request in `requests: list[tuple[str, str]]`.

- [ ] **Step 1: Write failing tests**

```python
def test_login_pins_tree(tmp_path, fake):            # fake = FakeGramps()
    c = GrampsClient("https://gw.test", "me", "pw", tmp_path / "gw.json", fake.transport)
    assert c.login() == "tree-a"
    assert json.loads((tmp_path / "gw.json").read_text()) == {"me": "tree-a"}

def test_login_refuses_other_tree(tmp_path, fake):
    (tmp_path / "gw.json").write_text('{"me": "tree-b"}')
    with pytest.raises(WrongTree, match="tree-a.*tree-b|tree-b.*tree-a"):
        GrampsClient("https://gw.test", "me", "pw", tmp_path / "gw.json", fake.transport).login()
    assert ("GET", "/api/people/") not in fake.requests

def test_old_server_refused(tmp_path, fake):
    fake.version = "3.21.1"
    with pytest.raises(TooOld, match="3.22.0"):
        GrampsClient(..., fake.transport).login()

def test_put_412_is_conflict(client, fake):
    h = fake.add("events", {...}); obj, etag = client.get("events", h)
    fake.objects["events"][h]["description"] = "changed"
    with pytest.raises(Conflict):
        client.update("events", h, obj, etag)

def test_latest_transaction_and_undo(client, fake): ...  # create -> latest_transaction id == fake.history[-1]["id"]; undo removes the object
def test_http_url_refused(kb_with_gramps_config): ...    # open_client with http://gw.example.net -> GrampsError naming https; http://localhost allowed
def test_unmapped_ged_refused(...): ...                  # tree_for -> GrampsError naming [gramps_web_trees]
```

- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_gramps_client.py -v`
- [ ] **Step 3: Implement** `FakeGramps` and `GrampsClient` per the interface. HTTP errors other than the ones mapped become `GrampsError(f"{method} {path}: {status} {message}")` where `message` is the JSON `error.message` if present. Never log or print the password or tokens.
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** `Add a Gramps Web client pinned to one tree`

---

### Task 3: GEDCOM dates to Gramps dates

**Files:**
- Create: `src/genea/gramps/dates.py`
- Test: `tests/test_gramps_dates.py`

**Interfaces:**
- Produces: `to_gramps_date(raw: str) -> dict` (raises `ValueError` for anything it cannot represent exactly) and `same_date(gramps_date: dict | None, raw: str | None) -> bool`.

Gramps date dict: `{"_class": "Date", "calendar": 0|1, "modifier": m, "quality": q, "dateval": [d, mo, y, False] (+ [d2, mo2, y2, False] for range/span), "text": "", "sortval": 0, "newyear": 0}`; modifier 0 none, 1 BEF, 2 AFT, 3 ABT, 4 BET…AND, 5 FROM…TO; quality 0, 1 for EST, 2 for CAL; calendar 1 for `@#DJULIAN@`. Missing day/month are 0. The server recomputes `sortval` (`recalc_date_sortvals`), so genea sends 0.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.parametrize("raw, mod, qual, val", [
    ("12 MAR 1883", 0, 0, [12, 3, 1883, False]),
    ("1883", 0, 0, [0, 0, 1883, False]),
    ("ABT 1880", 3, 0, [0, 0, 1880, False]),
    ("EST 1880", 0, 1, [0, 0, 1880, False]),
    ("CAL MAR 1880", 0, 2, [0, 3, 1880, False]),
    ("BEF 1900", 1, 0, [0, 0, 1900, False]),
    ("BET 1880 AND 1885", 4, 0, [0, 0, 1880, False, 0, 0, 1885, False]),
    ("FROM 1880 TO 1885", 5, 0, [0, 0, 1880, False, 0, 0, 1885, False]),
])
def test_to_gramps_date(raw, mod, qual, val): ...

def test_julian(): assert to_gramps_date("@#DJULIAN@ 1 JAN 1700")["calendar"] == 1

@pytest.mark.parametrize("raw", ["INT 1880 (about then)", "(sometime)", "@#DHEBREW@ 5600", "1731/32", "32 JAN 1880", ""])
def test_unrepresentable_raises(raw): ...

def test_same_date_ignores_sortval_and_text(): ...  # server copy with sortval set equals the raw string
def test_same_date_none(): assert same_date(None, None) and not same_date(None, "1880")
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement**, reusing `MONTHS` and the regexes' shapes from `genea/ingest/gedcom/dates.py` (import `MONTHS`; do not change that module). `same_date` compares `calendar`, `modifier`, `quality`, `dateval`; an empty Gramps date (`dateval` all zero, modifier 0, or the dict missing) equals `None`/empty raw.
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** `Convert GEDCOM dates to Gramps date objects`

---

### Task 4: Resolve proposals against Gramps and check for edits

**Files:**
- Create: `src/genea/gramps/resolve.py`
- Test: `tests/test_gramps_resolve.py`

**Interfaces:**
- Consumes: `GrampsClient` (Task 2), `to_gramps_date`, `same_date` (Task 3), `Proposal` (`analysis/models.py`).
- Produces:
  - `class Stale(Exception)` — message says what differs, with both values ("Gramps has date 1882, the proposal was made against 1881").
  - `@dataclass class Target: proposal: Proposal; reads: dict[str, tuple[str, dict, str]]` — key → `(kind, obj, etag)` for every object the writer will `PUT` (keys `"person"`, `"event"`, `"family"`, `"owner"`), plus `place_handle: str | None`, `source_handle: str | None`, `gramps_date: dict | None`.
  - `class Resolver(client)`: caches `places = list_all("places", "handle,gramps_id,profile")` and `sources = list_all("sources", "handle,gramps_id,title")` lazily.
    - `resolve(p: Proposal, new_people: dict[str, str]) -> Target` — raises `Stale` or `ValueError` (unrepresentable date, place/source not found).
  - `gramps_id(tree_id: str) -> str`: `"tree.ged#@I0012@"` → `"I0012"`.

Rules (spec §4, §5):
- Person/family by `find_by_gramps_id`; missing → `Stale("… is no longer in Gramps")`.
- Place: `payload["place"]` matched exactly against each place's `profile["name"]` (the display title Gramps' GEDCOM exporter writes as `PLAC`), or against `gramps_id` if the value looks like a Gramps place id (`^P\d+$`). No match or more than one → `ValueError("place … not in Gramps; add it there or give its Gramps id")`. Same for sources against `title`/`^S\d+$`.
- `update_event`: among the person's `event_ref_list`, the events whose type string equals the anchor's GEDCOM tag (map via a `TAG_TO_GRAMPS` dict for the tags in `INDIVIDUAL_EVENT_TAGS`, e.g. `BIRT → "Birth"`), `same_date` with the anchor date, place title equal to the anchor place, and description equal to the anchor description when the anchor has one. Exactly one → target; none → `Stale` stating the current values of the events of that type; several → `Stale("… more than once")`.
- `add_event`: `Stale` if the person already has an event of that type with `same_date`.
- `add_source_citation` with `event_type`: exactly one event of that type, else `Stale`.
- `link_child`: `Stale` if the child is already in the family's `child_ref_list`, or the child's `parent_family_list` holds another family. A `proposal:p-N` child is looked up in `new_people`; absent → `Stale("p-N was not written")`.
- `add_person`: `Stale` if some person has the same first name, surname and a birth with `same_date` (`list_all("people", "handle,gramps_id,profile")`, compare `profile.name_given`, `profile.name_surname`, `profile.birth.date`; the builder checks the profile date format against the fake and the live server).

- [ ] **Step 1: Write failing tests** (seed `FakeGramps` with I1 Jan Weiss, a Birth 1881 in "Brno", family F1):

```python
def test_update_event_target(resolver): t = resolver.resolve(update_birth_proposal, {}); assert t.reads["event"][1]["gramps_id"] == "E1"
def test_update_event_stale_when_user_changed_date(resolver, fake): ...      # Gramps birth now 1882 -> Stale matching "1882" and "1881"
def test_update_event_stale_when_twice(resolver, fake): ...
def test_place_must_exist(resolver): ...                                     # "Bratislava" -> ValueError "not in Gramps"
def test_place_by_gramps_id(resolver): ...                                   # "P0" -> handle of Brno
def test_add_event_duplicate_is_stale(resolver): ...
def test_link_child_already_linked_is_stale(resolver): ...
def test_link_child_new_person_needs_written_person(resolver): ...          # new_people={} -> Stale "p-3 was not written"
def test_add_person_duplicate_is_stale(resolver): ...
def test_unrepresentable_date_is_value_error(resolver): ...
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement** per the rules.
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** `Resolve proposals against Gramps Web and detect edits made since`

---

### Task 5: Writers, clean-up and write log

**Files:**
- Create: `src/genea/gramps/write.py`
- Modify: `src/genea/analysis/models.py`
- Test: `tests/test_gramps_write.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Modifies `Proposal`: `status: Literal["active", "withdrawn", "stale", "written", "failed"]`; new `written: WriteRecord | None = None`, `error: str | None = None`. `Analysis.counts()` adds `"written"` and `"failed"`.
- Produces in `models.py`: `class WrittenObject(BaseModel): kind: str; handle: str; gramps_id: str | None; created: bool; transaction: int`; `class WriteRecord(BaseModel): at: str; objects: list[WrittenObject]; pr: str | None = None`.
- Produces in `write.py`:
  - `write_proposal(client: GrampsClient, target: Target, log: WriteLog) -> WriteRecord` — raises `Conflict` (412) or `GrampsError`; on either, objects created for this proposal are deleted first (newest first, each deletion logged), and the exception's message gets " (cleaned up)" or " (clean-up failed: orphan <kind> <gramps_id>, delete it in Gramps)".
  - `class WriteLog(path: Path)` with `append(proposal_id: str, method: str, path: str, status: int, handle: str | None, gramps_id: str | None, transaction: int | None, changes: list | None) -> None`; one JSON line per call with `ts`.

Requests per operation (spec §6 table). Order is always: `create` new objects, then `update` the referencing object with the ETag from `Target.reads`. After each request, `latest_transaction(Class, handle)` gives the id for `WrittenObject.transaction`. Specifics:
- `add_event` / `add_person` birth: event `{"_class":"Event","type":{"_class":"EventType","string":"Birth"},"date":gramps_date,"place":place_handle or ""}`; the person gets `{"_class":"EventRef","ref":h,"role":{"_class":"EventRoleType","string":"Primary"}}`; for BIRT/DEAT set `birth_ref_index`/`death_ref_index` to the new ref's index if it is `-1`.
- `add_person`: create the person with `primary_name` (first name, one surname, `gender` 1 M / 0 F / 2 U, default 2) and no events, then create the birth event and `update` the person (fresh `get` for the ETag).
- `link_child`: `update` the family adding `{"_class":"ChildRef","ref":child_handle}`; then re-`get` the child and assert the family is in `parent_family_list`, adding it with a second `update` if the server did not (the builder records which one the live server does in a code comment).
- `add_note`: note `{"_class":"Note","text":{"_class":"StyledText","string":text}}`, then the person's `note_list`.
- `add_source_citation`: citation `{"_class":"Citation","source_handle":h,"page":page or ""}`, then the person's or the event's `citation_list`.

- [ ] **Step 1: Write failing tests** (fake server from Task 2, targets from Task 4's resolver):

```python
def test_update_event_writes_and_logs(...):  # fake event date now 1883; WriteRecord.objects == [WrittenObject(kind="events", created=False, transaction=<fake id>)]; writes.jsonl has one PUT line
def test_add_event_creates_then_links(...):   # fake.requests order: POST /api/events/, PUT /api/people/<h>; birth_ref_index set
def test_add_person_with_birth(...): ...
def test_link_child_sets_both_sides(...): ...
def test_412_marks_stale_and_cleans_up(...):  # fail_on PUT people 412 during add_event -> Conflict; the created event is gone from fake.objects; log has POST, PUT 412, DELETE
def test_cleanup_failure_names_orphan(...):   # fail_on DELETE 500 -> message contains "orphan events E"
def test_no_note_written_into_gramps(...):    # for update_event/add_event/link_child, fake.objects["notes"] stays empty
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS**; full suite (the `Proposal.status` change must not break `iteration.build`, which only takes `active`/`stale`).
- [ ] **Step 5: Commit** `Write proposals to Gramps Web with clean-up and a write log`

---

### Task 6: Snapshot PR

**Files:**
- Create: `src/genea/gramps/snapshot.py`
- Test: `tests/test_gramps_snapshot.py`

**Interfaces:**
- Produces:
  - `class SnapshotError(Exception)`.
  - `Runner = Callable[[list[str], Path], subprocess.CompletedProcess]` and `default_runner` (`subprocess.run(..., cwd=repo, capture_output=True, text=True, encoding="utf-8", check=False)`).
  - `repo_of(ged_path: Path, run: Runner = default_runner) -> Path` — `git rev-parse --show-toplevel`; not a git repo → `SnapshotError`.
  - `check_repo(repo: Path, run: Runner = default_runner) -> None` — on `main`, `git status --porcelain` empty, `git fetch origin` then `git rev-list --count main..origin/main` is `0`, `gh --version` works; each failure is a `SnapshotError` saying what to do.
  - `same_tree(a: bytes, b: bytes) -> bool` — equal after dropping the `0 HEAD` record (all lines up to the next level-0 line).
  - `@dataclass class Exports: ged: bytes; xml: bytes | None`.
  - `open_pr(repo: Path, ged_rel: Path, analysis: str, before: Exports, after: Exports, body: str, title: str, run: Runner = default_runner) -> str` — branch `genea/<today>-<analysis>` (suffix `-2`, `-3` … if it exists locally or on origin), before commit unless `same_tree(before.ged, <main's file>)`, after commit, `git push -u origin <branch>`, `gh pr create --base main --head <branch> --title <title> --body <body + before line>`, `git checkout main`; returns the PR URL (last line of `gh` output). Any failure after the branch exists: `git checkout main`, re-raise with the branch name so the user can finish it by hand. Files are written as bytes (no newline translation).

- [ ] **Step 1: Write failing tests** with a real temporary repo (`git init -b main`, a bare `origin` in `tmp_path`, initial commit with `latest.ged`) and a runner that passes `git` through and fakes `gh`:

```python
def test_two_commits_when_user_edits_unexported(repo, run):  # before.ged differs past HEAD -> git log main..branch has 2 commits, titles "Snapshot <today> before genea" then "genea: a-person-I1 (1 changes)"
def test_before_skipped_when_only_header_differs(repo, run): ...  # 1 commit
def test_gramps_xml_written_next_to_ged(repo, run): ...           # latest.gramps in the after commit; absent when after.xml is None
def test_returns_to_main_and_pr_url(repo, run): ...               # current branch main; returns "https://github.com/x/y/pull/7"
def test_check_repo_refuses_dirty_or_behind(repo, run): ...      # untracked file -> SnapshotError "not clean"; origin ahead -> "git pull"
def test_crlf_bytes_kept(repo, run): ...                          # export bytes with CRLF land unchanged
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** `Record Gramps Web exports as before/after snapshot PRs`

---

### Task 7: Apply, undo and snapshot orchestration

**Files:**
- Create: `src/genea/analysis/apply.py`
- Modify: `src/genea/analysis/models.py`, `src/genea/analysis/iteration.py`, `src/genea/analysis/api.py`
- Test: `tests/test_analysis_apply.py`

**Interfaces:**
- Consumes: Tasks 2–6, `store`, `tree_hash` (`api.py`).
- Modifies: `Analysis.last_build: LastBuild | None = None`, `class LastBuild(BaseModel): version: int; base_hash: str; proposals: list[str]` (ids applied), set at the end of `iteration.build`.
- Produces in `apply.py`:
  - `@dataclass class Plan: analysis: Analysis; proposals: list[Proposal]; diff_hunks: dict[str, str]` and `prepare(ctx: ToolContext, name: str, only: list[str] | None) -> Plan` — preconditions (spec §3.1): `last_build` exists, `last_build.base_hash == tree_hash(ctx, ged_file)`, `set(last_build.proposals) == {active ids}` (restricted to `only` when given; unknown ids → `ToolError`), the `.ged` mapped (`tree_for`), `check_repo` passes. Hunks are cut from `v<version>.diff` by the `GENEA … <proposal id>` marker each hunk carries.
  - `@dataclass class ApplyResult: written: list[str]; stale: dict[str, str]; failed: dict[str, str]; pr: str | None; message: str`.
  - `apply_analysis(ctx: ToolContext, plan: Plan, transport=None, run: Runner = default_runner) -> ApplyResult` — `open_client`; before-exports; resolve+write each proposal (order: `add_person` first, then the rest in proposal order, so `proposal:p-N` children resolve through `new_people`); proposal status updated and the analysis saved **after each proposal** (a crash keeps what was written); after-exports and `open_pr` only if something was written; `WriteRecord.pr` set; report rewritten.
  - `undo_proposal(ctx, name: str, proposal_id: str, transport=None, run=default_runner) -> ApplyResult` — `written` only; undo each `WrittenObject.transaction` newest first; any `Conflict` stops, leaves the proposal `written`, message names the Gramps object; success → `active`, `written=None`; then before/after snapshot PR titled `genea: undo <analysis> <proposal-id>`.
  - `snapshot_only(ctx, ged_file: str, transport=None, run=default_runner) -> str` — exports once, `before == after` skipped, title `Snapshot <today> from Gramps Web`, body says it holds no genea writes.
- Modifies `api.py` `_page`/report: a `written` proposal whose change is not visible in the current indexed tree is shown as `written, not in the current tree` (check: `update_event` — the person's event of that type has the new date/place in the index; `add_*` — a matching event/note/child/person exists in the index). Keep it a pure function `written_but_missing(ctx, analysis, p) -> bool` in `apply.py`.
- PR body: per written proposal `describe_operation` (from `changes/export.py`), `Why: <rationale>`, `Evidence: <cites>`; then "Not written:" with stale/failed reasons.

- [ ] **Step 1: Write failing tests** (`actx`/`analysis_kb` fixtures, KB config mapping `tree.ged` to user `me`, env password, `FakeGramps` seeded from the fixture tree's people/families/birth events, temp git repo holding a copy of `tree.ged` — point `[gramps_web_trees]` at a `.ged` inside the repo inside the KB):

```python
def test_apply_writes_and_opens_pr(...):       # 1 update_event -> status written, fake birth date changed, result.pr set, analysis.json has WriteRecord
def test_apply_refuses_outdated_build(...):    # tree.ged changed after build -> ToolError "analysis build", fake.requests == []
def test_apply_refuses_new_proposal_since_build(...): ...
def test_apply_refuses_dirty_or_behind_clone(...):  # -> ToolError before any Gramps request
def test_stale_and_written_mixed(...):         # one stale (user changed in fake), one written -> PR body lists "Not written: p-1 …"
def test_nothing_written_no_pr(...):           # all stale -> pr None, no branch
def test_link_child_to_new_person_in_same_apply(...):  # add_person p-1 + link_child proposal:p-1 -> both written, family has the new handle
def test_saved_after_each_proposal(...):       # transport raises on 2nd proposal's request -> 1st stays written in analysis.json
def test_undo_restores_and_reactivates(...):
def test_undo_conflict_leaves_written(...):
def test_build_skips_written(...):             # iteration.build after apply does not re-apply the written proposal
def test_written_but_missing(...): ...
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS**; full suite.
- [ ] **Step 5: Commit** `Apply and undo analysis proposals in Gramps Web`

---

### Task 8: CLI commands and docs

**Files:**
- Create: `src/genea/cli/grampscmds.py`
- Modify: `src/genea/cli/app.py`, `src/genea/cli/main.py`, `README.md`
- Test: `tests/test_cli_gramps.py`, `tests/test_mcp_analysis.py`

**Interfaces:**
- Consumes: Task 7 functions; `common.open_kb`, `common.open_tool_context`, `common.console` like `analysiscmds.py`.
- Produces commands (register `gramps_app = typer.Typer(...)` as `gramps` in `app.py`; `snapshot` on `app`; `apply`/`undo` on `analysis_app`):
  - `genea gramps login <ged>` — asks the password (`typer.prompt(hide_input=True)`), test-logs in, then `store_password`. Prints tree id and version, never the password.
  - `genea gramps check [<ged>]` — `info()` for one or every mapped `.ged`: user, tree, version, whether `Add`, `Edit` and `Delete` object permissions are present (warn if `Delete` is missing: clean-up and undo need it).
  - `genea snapshot <ged>` — `snapshot_only`, prints the PR URL.
  - `genea analysis apply <name> [--proposal ID]… [--dry-run] [--yes]` — prints the preview (per proposal: id, `describe_operation`, citations, its diff hunk), stops on `--dry-run`, else `typer.confirm("Write N change(s) to Gramps Web?")` unless `--yes`, then prints written/stale/failed and the PR URL plus "Merge the PR, git pull, then genea index."
  - `genea analysis undo <name> <proposal-id> [--yes]`.
- Errors (`ToolError`, `GrampsError`, `SnapshotError`, `CredentialsMissing`) print red and exit 1, as `_fail` does.

- [ ] **Step 1: Write failing tests** (CliRunner, the Task 7 fixtures; inject the fake transport and runner by monkeypatching `genea.cli.grampscmds.TRANSPORT`/`RUNNER` module globals that default to `None`/`default_runner`):

```python
def test_apply_dry_run_writes_nothing(...):    # exit 0, output has the hunk and "p-1", fake.requests has no POST/PUT
def test_apply_needs_confirmation(...):        # input="n\n" -> nothing written
def test_apply_yes_prints_pr(...): ...
def test_login_stores_password(...):           # fake keyring receives ("genea-gramps-web", "me", "pw"); output lacks "pw"
def test_check_warns_without_delete(...): ...
def test_mcp_has_no_apply_tool():              # in tests/test_mcp_analysis.py: no tool named apply/undo/apply_analysis
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement**; add a README section "Write approved proposals to Gramps Web": config example (`gramps_web_url`, `[gramps_web_trees]`, `gramps_web_xml_snapshot`), `uv sync --extra gramps`, `genea gramps login`, the apply → merge → `git pull` → `genea index` loop, undo, the Editor-role recommendation, the 3.22.0 floor.
- [ ] **Step 4: Run, expect PASS**; full suite `uv run pytest`.
- [ ] **Step 5: Commit** `Add CLI commands for Gramps Web write-back`

---

### Task 9: Live check (manual, needs the Gramps Web URL)

Not run by the implementer unless the user has given the URL and a throwaway or backed-up tree.

- [ ] `genea gramps login` / `gramps check` against the Pi: version ≥ 3.22.0, the right tree, Delete permission present.
- [ ] Compare `GET /api/exporters/ged/file` with the last desktop export in `fam_tree_ged`: `git diff --no-index` must show only the `HEAD` record (spec §9). If not, record what differs in the spec's open points.
- [ ] One `update_event` and one `add_event` on a test person: `apply --dry-run`, `apply`, the person in Gramps Web and desktop after sync, the PR's two commits, then `analysis undo` and the undo PR.
- [ ] Confirm the place title (`profile.name`) equals the `.ged` `PLAC` for a multi-level place, and whether `PUT /families` fills the child's `parent_family_list` (update the comment in `write.py`).
