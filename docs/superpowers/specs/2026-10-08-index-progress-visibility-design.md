# Index progress visibility (CLI + MCP) — Design

Date: 2026-10-08
Status: Draft, awaiting review

## 1. Goal

When the knowledge base (KB) is not indexed yet, or `genea index` is still running, a user who
connects Claude through `genea mcp` must learn **that** indexing is the problem and **how far along**
it is. Today they get neither:

- `genea mcp` exits with code 1 before the MCP handshake when `.genea/index.db` is missing
  (`common.open_tool_context`). Claude Code only shows `CONNECTION_CLOSED`; the stderr message is
  never seen. `index --rebuild` deletes the database first, so it causes the same failure.
- While an index run is in progress the server connects, but answers are silently based on a
  partial index (missing people read as "no such person").
- No process except `genea index` itself knows that a run is in progress or how far it got.

### Decisions

| Topic | Decision |
|---|---|
| MCP startup | The server always completes the handshake; the index is opened lazily on the first tool call |
| Partial index | Served, with an "index incomplete" note appended to every tool result (option 1 from brainstorming) |
| Missing index | Every tool returns an error that explains the state and shows progress if a run is active |
| Progress detail | JSON file `.genea/index.progress.json`, written by the indexer, read by anyone |
| "Is a run active?" | OS file lock on `.genea/index.lock`, held by `index_kb` for the whole run; released by the OS on crash |
| Interrupted run | `meta` row `index_state` = `running` at start, `complete` at the end of a successful run |
| New MCP tool | `index_status` (read-only), so Claude can answer "is it done yet?" |
| CLI | `genea index` shows a Rich progress bar; `genea status` shows the running / interrupted state |

### Non-goals

WAL mode / `busy_timeout`, building `--rebuild` into a temp file and swapping it in (separate
robustness change, "E" in brainstorming), indexing triggered from the MCP server, MCP progress
notifications or push updates, and changes to the PydanticAI agent's behaviour.

## 2. Index state model

One function decides the state; CLI and MCP both use it.

```
IndexState = "missing" | "building" | "interrupted" | "ready"
```

| DB file | lock held | `meta.index_state` | State | Meaning |
|---|---|---|---|---|
| missing | no | – | `missing` | never indexed: run `genea index` |
| missing | yes | – | `building` | first run or `--rebuild` in progress; nothing to query yet |
| exists | yes | any | `building` | run in progress; partial index is queryable |
| exists | no | `running` | `interrupted` | last run did not finish; partial index is queryable |
| exists | no | `complete` or absent | `ready` | normal |

An absent `index_state` (indexes built before this change) counts as `ready`.

## 3. Components

### 3.1 `genea/index/progress.py` (new)

- `IndexProgress` dataclass: `pid`, `started_at`, `updated_at` (ISO seconds), `phase`
  (`"scanning" | "indexing" | "linking"`), `done`, `total` (0 while scanning), `current`
  (relative path or `None`). Method `eta_seconds()`: during `indexing` with `done > 0`, the
  elapsed time since the indexing phase began divided by `done` × remaining; otherwise `None`.
  (The phase start is stored as `phase_started_at`.)
- `write_progress(kb, progress)`: write to `index.progress.json.tmp`, then `os.replace` (atomic on
  Windows and POSIX). Callers throttle: at most once per second, plus on every phase change.
- `read_progress(kb) -> IndexProgress | None`: `None` if missing or unparsable (a half-written or
  foreign file is never an error).
- `clear_progress(kb)`: delete, ignoring a missing file.
- `IndexLock`: context manager over `.genea/index.lock`. Exclusive, non-blocking:
  `msvcrt.locking(fd, LK_NBLCK, 1)` on Windows, `fcntl.flock(fd, LOCK_EX | LOCK_NB)` on POSIX.
  Raises `IndexBusy` when another process holds it. The file itself is never deleted (deleting a
  lock file races with the next acquirer).
- `is_index_running(kb) -> bool`: try to take the lock non-blocking; release at once; `True` if
  that failed.
- `index_state(kb) -> IndexState`: the table in §2. Reads `meta.index_state` with a short-lived
  plain `sqlite3` connection (no sqlite-vec needed, no schema check).

### 3.2 Indexer (`genea/index/indexer.py`)

`index_kb` gains `on_progress: Callable[[IndexProgress], None] | None` (replacing `on_file`) and:

1. Acquires `IndexLock` before anything else (including the `--rebuild` unlink). `IndexBusy`
   propagates to the CLI.
2. On `--rebuild`, a `PermissionError` from `unlink` (Windows: the file is open, typically by a
   running `genea mcp`) is re-raised as a new `IndexInUse` error with the message
   "The index is open in another program (e.g. Claude via `genea mcp`). Close it and retry."
3. After opening the DB: `meta.index_state = 'running'` (upsert, committed).
4. Reports progress through one helper that both writes the file (throttled) and calls
   `on_progress`: phase `scanning` (done = files scanned), phase `indexing` (done / total = len(pending),
   current = rel path, emitted before each file), phase `linking`.
5. After `rebuild_links` succeeds: `meta.index_state = 'complete'`.
6. `finally`: `clear_progress(kb)`, close the connection, release the lock. On an exception the
   state stays `running` → later seen as `interrupted`.

`reindex_file` (used by `changes apply`) also takes the lock (so it never runs concurrently with a
full index) but does not touch `index_state` or the progress file.

### 3.3 Status summary (`genea/index/status.py`, new)

`index_summary(kb) -> IndexSummary` with: `state`, `progress` (from the file, when `building`),
counts by kind/status, number of people, and the problem files. It is the query code currently
inline in `cli/indexcmds.py:status`, moved so the MCP tool and the CLI share it. Counts are
`None` when the DB file is missing.

### 3.4 MCP server (`genea/mcp_server.py`, `cli/mcpcmds.py`)

- `genea mcp` no longer calls `open_tool_context`. It opens the KB (a missing KB root is still an
  immediate exit 1: that is a configuration error, not a timing problem) and starts the server with
  a lazy `IndexGate`.
- `IndexGate.context() -> ToolContext`: computes `index_state(kb)`.
  - `missing` → `McpToolError("The knowledge base is not indexed yet. Run `genea --kb <kb> index`, then try again.")`
  - `building` with no DB file → `McpToolError` with progress, e.g.
    "The index is being built: 1 240 / 5 310 files (23 %), started 10:02, ETA ~35 min. Try again later."
  - otherwise opens the `ToolContext` once and caches it (same event-loop thread as today).
    `SchemaMismatch` → `McpToolError` with its message (not cached, so a rebuild is picked up).
- `IndexGate.note() -> str | None`: the footer for `building` ("Note: the index is still being built
  (23 %); results may be incomplete.") and `interrupted` ("Note: the last indexing run did not finish;
  results may be incomplete. Run `genea index`."). Recomputed on every call (one lock probe and one
  `meta` read: cheap).
- The `run` wrapper appends `"\n\n" + note` to successful results when a note applies. Errors are
  not annotated.
- New tool `index_status()`: returns the `IndexSummary` as text: state, progress with ETA, file
  counts, people count, number of problem files (not the full list). It works in every state,
  including `missing`.
- `MCP_INSTRUCTIONS` gains one rule: "If a tool result says the index is missing, being built or
  incomplete, tell the user and do not treat missing results as evidence. `index_status` reports
  progress." The server also appends the state at startup ("Index state at startup: building, 23 %.")
  to the instructions.

### 3.5 CLI

- `genea index`: Rich `Progress` with a spinner during scanning, then a bar with
  `done/total`, ETA and the current file during indexing, and a "linking" step. Final report
  lines are unchanged. `IndexBusy` → "Another `genea index` is already running for this knowledge
  base." exit 1. `IndexInUse` → its message, exit 1.
- `genea status`: uses `index_summary`.
  - `building`: banner "Indexing in progress: 1 240 / 5 310 files (23 %), ETA ~35 min, now: <file>";
    then the table if the DB exists.
  - `interrupted`: yellow banner "The last indexing run did not finish. Run `genea index`." then the table.
  - `missing`: unchanged message, exit 1.

## 4. Error handling

- An unreadable or half-written progress file is treated as absent (state still comes from the lock).
- A crash leaves the lock released (by the OS) and `index_state = running` → `interrupted`. The stale
  progress file is ignored when the lock is free and is overwritten or cleared by the next run.
- Lock probe failures other than "held" (e.g. `.genea` not writable) → treated as not running, so the
  MCP server still serves.

## 5. Testing

- `progress.py`: write/read round trip; garbage file → `None`; `IndexLock` held by one handle makes
  a second acquire raise `IndexBusy` and `is_index_running` return `True`; ETA math.
- `index_state`: each row of the table in §2 (lock taken by the test, `meta` set by hand).
- Indexer: `on_progress` sees scanning → indexing (correct totals) → linking; progress file is gone
  and `index_state = complete` afterwards; an ingester that raises an uncaught error leaves
  `running`; a second `index_kb` while the lock is held raises `IndexBusy`.
- MCP (`test_mcp_server.py`, in-process): with no index the server lists tools and every tool,
  including `index_status`, answers; query tools return the "not indexed" error. With the lock held
  and an existing index, results carry the note. `index_status` in the `ready` state.
- `test_cli_mcp.py`: `test_not_indexed_exits_1_...` is replaced by a stdio end-to-end check that the
  server starts without an index and `find_person` returns the "not indexed" error.
- CLI: `status` banners for `building` and `interrupted`; `index` exits 1 with the message when the
  lock is held.

## 6. README

"Use with Claude (MCP)": the server can be connected before or during indexing; Claude reports the
state and `index_status` shows progress. Mention that `index --rebuild` on Windows needs the MCP client
closed.
