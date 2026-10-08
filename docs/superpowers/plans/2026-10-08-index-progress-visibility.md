# Index Progress Visibility Implementation Plan

> Executed natively in one session at the user's request ("write the implementation"), TDD per task.

**Goal:** `genea mcp` always connects and tells Claude whether the index is missing, being built
(with progress) or incomplete; `genea index` / `genea status` show progress.

**Spec:** `docs/superpowers/specs/2026-10-08-index-progress-visibility-design.md`

**Tech Stack:** Python 3.12, sqlite3, `msvcrt` / `fcntl` locks, Rich, official `mcp` SDK, pytest.

## Global Constraints

- The MCP server stays read-only; stdout carries only the MCP protocol.
- Indexes built before this change (no `meta.index_state`) count as `ready`.
- Works on Windows and POSIX.

## Review Focus

- A KB path with spaces (`D:\Foto DB\...`) must work for the read-only `meta` probe.
- A crashed indexer must not block the next run (the OS releases the lock).
- A momentary lock probe by `status`/MCP must not make a starting `genea index` fail (the indexer retries briefly).
- `SchemaMismatch` during `index_kb` must release the lock.
- A garbage progress file must not break `status` or the MCP server.

## Tasks

1. **`genea/index/progress.py`** — `IndexProgress` (+ `eta_seconds`, `describe`), `write_progress` /
   `read_progress` / `clear_progress`, `IndexLock` (with short retry), `IndexBusy`, `IndexInUse`,
   `is_index_running`, `index_state`. Tests: `tests/test_progress.py`.
2. **Indexer** — `index_kb(..., on_progress=)` under the lock; `meta.index_state` running/complete
   (`db.set_index_state`); progress phases; rebuild `PermissionError` → `IndexInUse`; `reindex_file`
   takes the lock. Tests: `tests/test_indexer.py`.
3. **`genea/index/status.py`** — `IndexSummary` / `index_summary`. CLI `status` banners and `index`
   progress bar + busy messages. Tests: `tests/test_cli_index.py`.
4. **MCP** — `IndexGate` (lazy context, errors, note), `index_status` tool, instructions rule + startup
   state, `genea mcp` starts without an index. Tests: `tests/test_mcp_server.py`, `tests/test_cli_mcp.py`.
5. **README** — "Use with Claude (MCP)" section.
