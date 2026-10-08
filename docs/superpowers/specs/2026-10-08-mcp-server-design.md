# genea MCP Server (read-only) — Design

Date: 2026-10-08
Status: Approved; implemented on jrumanov/claude-mcp

## 1. Goal

Let Claude Code and Claude Desktop use a genea knowledge base (KB) directly. Claude
connects to a local MCP server and calls the existing read-only genealogy tools
(people, relatives, timeline, document search, images, conflicts). Claude is then the
agent; genea supplies the data and citations. The built-in PydanticAI agent and the
Ollama path are unchanged.

The original design (`2026-10-06-genealogy-agent-design.md`) listed an "MCP adapter"
as a later item and required that `tools/` have no LLM dependency. This spec delivers
that adapter.

### Decisions

| Topic | Decision |
|---|---|
| Transport | stdio only. Claude launches the server as a subprocess; no port, no auth |
| Scope | Read-only: 8 tools. `write_note` and `propose_change` are not exposed |
| Implementation | Official `mcp` Python SDK, `FastMCP`-style server in `genea/mcp_server.py` |
| Packaging | Optional extra `mcp` (`uv sync --extra mcp`); the base install does not need it |
| KB selection | Existing global `--kb` option / config discovery, same as every other command |

### Non-goals

HTTP/SSE transport, remote or multi-user use, authentication, exposing write tools
(`write_note`, `propose_change`, `changes apply`), MCP resources and prompts beyond
server `instructions`, indexing from inside the server, and changes to the PydanticAI
agent's behaviour.

## 2. Architecture

```
Claude Code / Desktop ──stdio──> genea mcp ──> mcp_server.py ──> tools/* ──> KB index (SQLite)
                                                    │
                                                    └── tool_runner.py (shared with agent/core.py)
```

- `genea/mcp_server.py` (new): builds the server, registers 8 tools, runs it on stdio.
- `genea/tool_runner.py` (new): the error handling and size limiting that is currently
  private to `agent/core.py` — `_dump`, `_cut_strings`, `_shrink`, `_render`, `_call`,
  `_error`, `TRUNCATION_MARKER` — moved here and made independent of PydanticAI's
  `RunContext` (they take a `ToolContext` instead). `agent/core.py` imports them from
  here. This is the only refactor; behaviour and the existing tests must not change.
- `genea/cli/mcpcmds.py` (new): the `genea mcp` command; registered in `cli/main.py`.
- `tools/*` is untouched and keeps no LLM or MCP dependency.

### Module boundaries

| Unit | Does | Used via | Depends on |
|---|---|---|---|
| `tool_runner` | Runs a tool function, turns `ToolError` and unexpected exceptions into `{"error": ...}`, trims output to `tool_output_chars` keeping `sources` | `run_tool(ctx, fn, *args)` -> `str` | `tools.base`, `tools.models` |
| `mcp_server` | Registers tools, supplies `instructions`, owns the lifecycle | `build_server(tool_ctx) -> server`, `serve(tool_ctx)` | `mcp` SDK, `tool_runner`, `tools/*`, `agent.prompt` |
| `cli/mcpcmds` | Opens the KB and index, prints setup errors to stderr, calls `serve` | `genea mcp` | `cli.common`, `mcp_server` |

## 3. Tools exposed

Same names, arguments and docstrings as the agent's tools, so behaviour matches what
the CLI agent already does:

`find_person`, `get_person`, `relatives`, `timeline`, `search_documents`,
`read_document`, `find_images`, `find_conflicts`.

Each wrapper calls `run_tool(ctx, tools.<module>.<fn>, ...)` and returns the JSON
string (the same payload the agent receives, including `sources` and the truncation
marker). Tool descriptions come from one place: the docstrings are shared with the
agent registrations (a small table of `(name, function, description)` used by both
registrations) so the two cannot drift. If sharing the table proves awkward during
implementation, duplicate the docstrings and add a test that compares them.

Tool wrappers are `async def`: the SDK runs plain functions in worker threads and the
SQLite connection is bound to the event-loop thread.

Tool failures carry the `{"error": "..."}` body (the agent's current behaviour) so Claude
reads the message and corrects itself. They are raised as the SDK's `ToolError`, so the
client gets a result with `is_error=True` whose text is `Error executing tool <name>: `
followed by the `{"error": ...}` JSON body.

### Server instructions

`agent/prompt.py:SYSTEM_PROMPT` is reviewed for MCP use. Parts that describe writing
notes or proposing changes are removed from what the MCP server sends, since those
tools are absent. If the prompt is one string today, split it into a shared core plus
an agent-only curator section; the agent's prompt text must stay identical.

## 4. Startup and failure behaviour

- `genea --kb <path> mcp` opens the KB and the index once and serves until stdin
  closes. One SQLite connection for the process; tool wrappers are async but their bodies
  are synchronous, so calls run one at a time on the event loop, which is sufficient for a single local client.
- **Not indexed:** exit 1 with the existing "not indexed yet. Run `genea index` first"
  message on stderr. **Schema mismatch:** same handling as `open_tool_context`.
- **stdout is reserved for the protocol.** Nothing else may print to stdout: no Rich
  console output, no banners, no stray `print`. The command sets
  `PYDANTIC_AI_NO_BANNER` (already done in `cli/main.py`), routes logging to stderr,
  and a test asserts that stdout contains only protocol frames. The global callback's
  `use_utf8_stdout()` only sets the stdout encoding to UTF-8 and is harmless here.
- **Embeddings:** the server builds the normal `OllamaEmbedder`. If Ollama or the
  embedding model is missing, `search_documents` already degrades to full-text search
  and returns a `note`; the server must start and work in that state.
- **Privacy:** the server sends tool results to whichever Claude client is connected.
  `genea mcp` prints a one-line notice to stderr at startup (suppressed by the existing
  `privacy_notice = false`), the same idea as the cloud-model notice in `ask`/`chat`.
- **Missing extra:** if `mcp` is not installed, the command exits 1 with
  "install the MCP extra: `uv sync --extra mcp`".

## 5. Packaging and docs

- `pyproject.toml`: `[project.optional-dependencies] mcp = ["mcp>=2.3"]`. Verified against the
  2.x SDK: the server class is `MCPServer` in `mcp.server.mcpserver` and the lower bound is
  `mcp>=2.3`; the 1.x `FastMCP` API does not carry over.
- README gets an "Use with Claude" section:
  - Claude Code: `claude mcp add genea -- uv run --directory <repo> --extra mcp genea --kb <kb> mcp`
  - Claude Desktop: the equivalent `claude_desktop_config.json` entry, using absolute
    paths (Windows paths with escaped backslashes).
  - A note that the KB must be indexed first and that Ollama is optional (search falls
    back to keywords).
  - A note that the server is read-only and that notes and proposed changes still go
    through the genea CLI.
- The 2026-10-06 spec's "later" line for MCP is updated to point at this spec.

## 6. Testing

All tests run offline against the fixture KB with `FakeEmbedder`, like the existing
suite.

1. **`tool_runner` characterization:** the existing agent tests keep passing after the
   move; add direct tests for error conversion and truncation with `sources` preserved.
2. **Registration:** building the server yields exactly the 8 expected tool names, and
   neither `write_note` nor `propose_change` is present. This is the read-only guard.
3. **In-process calls:** call each tool through the server with the fixture KB and
   compare against calling `tools.*` directly (payload parity); one bad person id
   returns the `{"error": ...}` body rather than raising.
4. **Stdio smoke test:** spawn `genea mcp` as a subprocess against a prepared fixture
   KB, run the MCP initialize handshake, list tools, call `find_person`, and assert that
   stdout carried only protocol messages. Skipped if the `mcp` package is absent.
5. **Startup errors:** not-indexed KB exits 1 with the message on stderr and nothing on
   stdout.

A manual check at the end (not in CI): register the server with Claude Code against a
real KB and ask a question that needs `find_person` plus `search_documents`.

## 7. Risks and open points

- **SDK version:** `mcp` 2.x is a new major version; the API in section 5 must be
  confirmed first, and the implementation plan should start with a short spike that
  runs a hello-world stdio server on Windows.
- **Windows stdio quirks:** encoding of non-ASCII names (Czech diacritics are common in
  these KBs) must be tested end to end through the stdio smoke test.
- **Output size:** `tool_output_chars` (default 12000) was tuned for local models;
  Claude can take more. Keep the default for now; revisit only if truncation shows up
  in real use.
- **Concurrent clients:** two Claude clients each spawn their own server process on the
  same SQLite index. Reads are fine; the server never writes. Not tested beyond that.
