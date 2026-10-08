# genea MCP Server (read-only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `genea mcp` serves the 8 read-only genealogy tools to Claude Code / Claude Desktop over stdio.

**Architecture:** The error handling and output trimming that live privately in `agent/core.py` move into a shared `genea/tool_runner.py`. A new `genea/mcp_server.py` registers thin async wrappers around `tools/*` on an `MCPServer` (official `mcp` 2.x SDK). A new `genea mcp` CLI command opens the KB and runs the server on stdio. `tools/*` is untouched.

**Tech Stack:** Python 3.12, `mcp>=2.3` (optional extra), Typer, PydanticAI (existing agent), pytest.

**Spec:** `docs/superpowers/specs/2026-10-08-mcp-server-design.md`

## Verified SDK facts (the spec's "spike" is done)

Checked against `mcp` 2.3.0 on Windows with a hello-world stdio server. Do not re-derive; use these.

**Dry run (2026-10-08):** Tasks 1-4 were applied verbatim to a throwaway copy of the repo and the full suite ran: 250 passed, 2 skipped, including the stdio end-to-end test; the agent prompt hash was unchanged. The code below is that verified version.

- `FastMCP` is gone; the class is `from mcp.server.mcpserver import MCPServer`. Constructor: `MCPServer(name, instructions=...)`.
- Register with `@server.tool(structured_output=False)` (plain text content, no `{"result": ...}` wrapper). `server.run("stdio")` is synchronous and blocks.
- **Sync tool functions are run in anyio worker threads.** genea's SQLite connection is opened on the main thread and is not thread-safe across threads, so every tool wrapper MUST be `async def` (it then runs on the event-loop thread, the same thread that opened the connection). The tool bodies stay synchronous calls.
- Raising `from mcp.server.mcpserver.exceptions import ToolError` inside a tool: over the wire it becomes a result with `is_error=True` and text `Error executing tool <name>: <message>`. Calling `await server.call_tool(...)` in-process **raises** `ToolError` instead of returning `is_error`.
- Introspection: `await server.list_tools()` gives objects with `.name` and `.description`.
- Client side (for the smoke test): `from mcp import ClientSession, StdioServerParameters`, `from mcp.client.stdio import stdio_client`; `stdio_client(params, errlog=<open file>)`; `session.initialize()` returns an object with `.instructions`; `session.call_tool(name, args)` returns an object with `.is_error` and `.content[0].text`. `StdioServerParameters(command=..., args=[...], env=...)`: its default env is a small safe subset, so pass `env=dict(os.environ)` explicitly.

## Global Constraints

- Read-only: exactly these 8 tools and no others: `find_person`, `get_person`, `relatives`, `timeline`, `search_documents`, `read_document`, `find_images`, `find_conflicts`. `write_note` and `propose_change` must never be registered.
- stdio only; no HTTP/SSE, no auth.
- `mcp` is an optional extra: `mcp = ["mcp>=2.3"]` under `[project.optional-dependencies]`; the base install must still import and run without it. The `mcp` package is also added to the `dev` dependency group so the suite exercises it.
- `tools/*` keeps no LLM or MCP dependency; `agent/core.py` behaviour and the agent's `SYSTEM_PROMPT` text must not change.
- stdout is reserved for protocol frames; all human-readable output of `genea mcp` goes to stderr.
- All tests run offline against the fixture KB (`FakeEmbedder`), like the existing suite. Run tests with `uv run pytest -q`.
- Git: one commit per task is fine on the branch `jrumanov/claude-mcp`; the PR is squashed to one commit and rebase-merged (see the user's git workflow notes). Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

Failure modes the spec implies that the tasks' main tests would not otherwise exercise. Each has a pinning test in the task named in brackets.

1. Czech names (`Jan Novák`) must survive the stdio round trip unmangled on Windows. [Task 4, smoke test]
2. Ollama down or embedding model missing: `search_documents` still answers (keywords only) with a `note`, and the server still starts. [Task 3, `test_search_degrades_without_embeddings`]
3. Invalid tool arguments (unknown `relation`, missing required argument) return an error to Claude, and the server keeps serving the next call. [Task 4, smoke test]
4. Several tool calls in a row on one connection (SQLite thread affinity: async wrappers, not sync). [Task 4, smoke test makes four calls]
5. Non-indexed KB or missing `mcp` extra: exit code 1, message on stderr, **nothing** on stdout (a client would otherwise parse garbage as protocol). [Task 4, CLI tests]

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/genea/tool_runner.py` | Create | `run_tool(ctx, fn, ...) -> ToolOutput`; `render`, `shrink`, `dump`, `TRUNCATION_MARKER`. No PydanticAI or MCP imports. |
| `src/genea/agent/core.py` | Modify | Import from `tool_runner`; `_call` becomes a one-liner. |
| `src/genea/agent/prompt.py` | Modify | Split into shared pieces; add `MCP_INSTRUCTIONS`; `SYSTEM_PROMPT` text unchanged. |
| `src/genea/mcp_server.py` | Create | `build_server(ctx) -> MCPServer`, `serve(ctx)`. |
| `src/genea/cli/mcpcmds.py` | Create | The `genea mcp` command. |
| `src/genea/cli/main.py` | Modify | Register `mcpcmds`. |
| `pyproject.toml`, `uv.lock` | Modify | `mcp` extra + dev dependency. |
| `tests/test_tool_runner.py` | Create | Runner behaviour. |
| `tests/test_prompt.py` | Create | Prompt split. |
| `tests/test_mcp_server.py` | Create | Registration, parity, errors, degradation. |
| `tests/test_cli_mcp.py` | Create | CLI startup errors + stdio smoke test. |
| `tests/test_agent.py` | Modify | Import `shrink` from its new home. |
| `README.md`, spec files | Modify | "Use with Claude" docs; spec corrections. |

---

### Task 1: Extract `tool_runner` from `agent/core.py`

**Files:**
- Create: `src/genea/tool_runner.py`
- Create: `tests/test_tool_runner.py`
- Modify: `src/genea/agent/core.py` (the block from `TRUNCATION_MARKER = ...` through the end of `_call`, currently lines 35-107, plus imports)
- Modify: `tests/test_agent.py:132-134`

**Interfaces:**
- Produces:
  - `class ToolOutput(NamedTuple): text: str; is_error: bool`
  - `run_tool(ctx: ToolContext, fn, *args, **kwargs) -> ToolOutput`
  - `render(ctx: ToolContext, result: BaseModel) -> str`
  - `error_text(ctx: ToolContext, message: str) -> str`
  - `shrink(data: dict[str, Any], limit: int) -> str`, `dump(data: Any) -> str`, `TRUNCATION_MARKER: str`
- Consumes: `ToolContext`, `ToolError` from `genea.tools.base`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_tool_runner.py`:

```python
import json
from dataclasses import replace

from genea.kb import KnowledgeBase
from genea.tool_runner import TRUNCATION_MARKER, run_tool
from genea.tools import people


def test_run_tool_returns_result_json(tools):
    out = run_tool(tools, people.find_person, "Jan Novák")
    assert out.is_error is False
    assert json.loads(out.text)["candidates"]


def test_tool_error_becomes_error_text(tools):
    out = run_tool(tools, people.get_person, "tree.ged#@I999@")
    assert out.is_error is True
    assert "find_person" in json.loads(out.text)["error"]


def test_unexpected_exception_becomes_error_text(tools):
    def boom(ctx):
        raise RuntimeError("corrupt vec table")

    out = run_tool(tools, boom)
    assert out.is_error is True
    assert json.loads(out.text)["error"] == "boom failed: RuntimeError: corrupt vec table"


def test_oversize_result_is_truncated_and_keeps_sources(tools):
    small = replace(tools, kb=KnowledgeBase(tools.kb.root, replace(tools.kb.settings, tool_output_chars=600)))
    out = run_tool(small, people.get_person, "tree.ged#@I3@")
    assert out.is_error is False
    assert out.text.endswith(TRUNCATION_MARKER)
    body = json.loads(out.text[: -len(TRUNCATION_MARKER)])
    assert body["sources"] and body["truncated"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_tool_runner.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'genea.tool_runner'`).

- [ ] **Step 3: Create `src/genea/tool_runner.py`** (the trimming code is moved verbatim from `agent/core.py`, only the names lose their underscore and `render`/`error_text` take a `ToolContext`):

```python
"""Run a tool function for a model or an MCP client: failures and oversize results become plain text."""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from pydantic import BaseModel

from genea.tools.base import ToolContext, ToolError

TRUNCATION_MARKER = " …[truncated: narrow the query]"


class ToolOutput(NamedTuple):
    text: str
    is_error: bool


def dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def cut_strings(value: Any, cap: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= cap else value[:cap] + "…"
    if isinstance(value, list):
        return [cut_strings(v, cap) for v in value]
    if isinstance(value, dict):
        return {k: cut_strings(v, cap) for k, v in value.items()}
    return value


def shrink(data: dict[str, Any], limit: int) -> str:
    """Fit a tool result into `limit` characters, always keeping `sources` (deduplicated).

    The output stays valid JSON followed by TRUNCATION_MARKER (the only text allowed beyond `limit`).
    Order of trimming: long strings, then list items, then everything but `sources`; at least one
    source is always kept, even if that alone exceeds a tiny limit.
    """
    sources: list[Any] = []
    for src in data.get("sources", []):
        if src not in sources:
            sources.append(src)
    data = {**data, "sources": sources, "truncated": True}

    def fits(d: dict[str, Any]) -> bool:
        return len(dump(d)) <= limit

    for cap in (2000, 500, 200, 80):
        data = cut_strings(data, cap)
        if fits(data):
            return dump(data)
    lists = [k for k, v in data.items() if k != "sources" and isinstance(v, list)]
    for key in lists:
        while len(data[key]) > 1 and not fits(data):
            data[key] = data[key][: len(data[key]) // 2]
        if fits(data):
            return dump(data)
    data = {"sources": data["sources"], "truncated": True}
    while len(data["sources"]) > 1 and not fits(data):
        data["sources"] = data["sources"][: len(data["sources"]) // 2]
    return dump(data)


def render(ctx: ToolContext, result: BaseModel) -> str:
    text = result.model_dump_json(exclude_none=True)
    limit = ctx.kb.settings.tool_output_chars
    if len(text) <= limit:
        return text
    return shrink(result.model_dump(mode="json", exclude_none=True), limit) + TRUNCATION_MARKER


def error_text(ctx: ToolContext, message: str) -> str:
    """A failure is a normal tool result, so repeated failures never exhaust a per-tool retry budget."""
    limit = ctx.kb.settings.tool_output_chars
    if len(message) > limit:
        message = message[:limit] + "…"
    return dump({"error": message})


def run_tool(ctx: ToolContext, fn, *args, **kwargs) -> ToolOutput:
    try:
        return ToolOutput(render(ctx, fn(ctx, *args, **kwargs)), False)
    except ToolError as exc:
        return ToolOutput(error_text(ctx, str(exc)), True)
    except Exception as exc:  # noqa: BLE001 - one bad file or index must not crash the agent loop or server
        return ToolOutput(error_text(ctx, f"{fn.__name__} failed: {type(exc).__name__}: {exc}"), True)
```

- [ ] **Step 4: Re-point `agent/core.py` at it.**
  1. Delete everything from the line `TRUNCATION_MARKER = " …[truncated: narrow the query]"` down to and including the old `_call` function (the lines just before `def build_agent`). Replace with:

```python
def _call(ctx: RunContext[ToolContext], fn, *args, **kwargs) -> str:
    return run_tool(ctx.deps, fn, *args, **kwargs).text
```

  2. Imports: delete the line `from pydantic import BaseModel`, and replace `from genea.tools.base import ToolContext, ToolError` with these two lines (keep everything else, including `json`, `Any` and `Literal`, which are still used):

```python
from genea.tool_runner import run_tool
from genea.tools.base import ToolContext
```
  3. In `tests/test_agent.py` change the test at lines 132-134 to import from the new place:

```python
def test_shrink_trims_lists_and_keeps_unicode_intact():
    from genea.tool_runner import shrink as _shrink
```

- [ ] **Step 5: Run the whole suite (behaviour must be unchanged)**

Run: `uv run pytest -q`
Expected: 231 passed (227 before + 4 new), 2 skipped, 15 deselected.

- [ ] **Step 6: Commit**

```bash
git add src/genea/tool_runner.py src/genea/agent/core.py tests/test_tool_runner.py tests/test_agent.py
git commit -m "Extract tool_runner from the agent so MCP can share it

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Split the system prompt and add `MCP_INSTRUCTIONS`

**Files:**
- Modify: `src/genea/agent/prompt.py` (whole file)
- Create: `tests/test_prompt.py`

**Interfaces:**
- Produces: `SYSTEM_PROMPT: str` (byte-identical to today), `MCP_INSTRUCTIONS: str` (same rules 1, 2, 3, 5; rule 4 says the server is read-only; no mention of `propose_change`, `write_note` or `genea changes`).

- [ ] **Step 1: Record the current prompt hash**

Run: `uv run python -c "import hashlib; from genea.agent.prompt import SYSTEM_PROMPT as s; print(hashlib.sha256(s.encode()).hexdigest())"`
Write the printed hash down; Step 4 compares against it.

- [ ] **Step 2: Write the failing tests** — create `tests/test_prompt.py`:

```python
from genea.agent.prompt import MCP_INSTRUCTIONS, SYSTEM_PROMPT

SHARED = ("Always call find_person first", "Documented:", "Inferred:", "Answer in the language")


def test_shared_rules_are_in_both():
    for needle in SHARED:
        assert needle in SYSTEM_PROMPT
        assert needle in MCP_INSTRUCTIONS


def test_agent_prompt_keeps_write_rules():
    assert "propose_change" in SYSTEM_PROMPT
    assert "write_note" in SYSTEM_PROMPT


def test_mcp_instructions_mention_no_write_tools():
    for word in ("propose_change", "write_note", "genea changes"):
        assert word not in MCP_INSTRUCTIONS
    assert "read-only" in MCP_INSTRUCTIONS
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_prompt.py -q`
Expected: FAIL (`ImportError: cannot import name 'MCP_INSTRUCTIONS'`).

- [ ] **Step 4: Rewrite `src/genea/agent/prompt.py`**:

```python
_HEAD = """\
You are genea, a careful genealogy research assistant for ONE family-history knowledge base
(a GEDCOM family tree, text documents and photo metadata). You can only see it through your tools.

Rules:
1. Every genealogical fact you state must have a citation copied from tool results, written like
   [tree.ged:L1042] (GEDCOM line), [docs/matrika.pdf p.3] (document page) or [photos/x.jpg].
   If the tools return nothing relevant, say so. Never use outside knowledge about these people.
2. People are identified by ids like 'tree.ged#@I12@'. Always call find_person first. If several
   candidates fit and the question does not say which one is meant, list them (name, life dates, id)
   and ask the user to choose.
3. Keep evidence and reasoning apart: when you draw conclusions, use the headings
   "Documented:" and "Inferred:".
"""

_AGENT_RULE_4 = """\
4. You cannot change any file yourself. To suggest a correction, call propose_change, then give the
   user the change id and tell them to review it with `genea changes show <id>`. Never claim that a
   change was made. Use write_note only when the user asks you to save findings.
"""

_MCP_RULE_4 = """\
4. This server is read-only: you cannot change any file. To suggest a correction, describe it in
   your answer with its supporting citations. Never claim that a change was made.
"""

_TAIL = """\
5. Answer in the language of the user's question. Be concise.
"""

SYSTEM_PROMPT = _HEAD + _AGENT_RULE_4 + _TAIL
MCP_INSTRUCTIONS = _HEAD + _MCP_RULE_4 + _TAIL
```

- [ ] **Step 5: Verify the agent prompt is unchanged and tests pass**

Run: `uv run python -c "import hashlib; from genea.agent.prompt import SYSTEM_PROMPT as s; print(hashlib.sha256(s.encode()).hexdigest())"`
Expected: the same hash as Step 1. If it differs, fix whitespace until it matches.

Run: `uv run pytest tests/test_prompt.py tests/test_agent.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/genea/agent/prompt.py tests/test_prompt.py
git commit -m "Split the system prompt so the MCP server gets a read-only variant

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `mcp_server.py` — the 8 read-only tools

**Files:**
- Modify: `pyproject.toml`, `uv.lock`
- Create: `src/genea/mcp_server.py`
- Create: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `run_tool` / `ToolOutput` (Task 1), `MCP_INSTRUCTIONS` (Task 2), `ToolContext`.
- Produces: `build_server(ctx: ToolContext) -> MCPServer`, `serve(ctx: ToolContext) -> None`.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`: under `[project.optional-dependencies]` add `mcp = ["mcp>=2.3"]` (next to `cloud`), and add `"mcp>=2.3"` to the `dev` list in `[dependency-groups]`.

Run: `uv sync --extra mcp`
Expected: resolves and installs `mcp`; `uv.lock` updated.

- [ ] **Step 2: Write the failing tests** — create `tests/test_mcp_server.py`:

```python
import asyncio
import json
from dataclasses import replace

import pytest

pytest.importorskip("mcp")

from mcp.server.mcpserver.exceptions import ToolError as McpToolError  # noqa: E402
from pydantic_ai.models.test import TestModel  # noqa: E402

from genea.agent.core import build_agent  # noqa: E402
from genea.index.embed import EmbeddingError  # noqa: E402
from genea.mcp_server import build_server  # noqa: E402
from genea.tool_runner import run_tool  # noqa: E402
from genea.tools import conflicts, people, search  # noqa: E402

READ_ONLY = {"find_person", "get_person", "relatives", "timeline", "search_documents",
             "read_document", "find_images", "find_conflicts"}
JAN = "tree.ged#@I12@"

# (tool name, MCP arguments, tools.* function, positional args the wrapper must pass to it)
CASES = [
    ("find_person", {"name": "Jan Novák", "born_around": 1900}, people.find_person, ("Jan Novák", 1900, None)),
    ("get_person", {"person_id": JAN}, people.get_person, (JAN,)),
    ("relatives", {"person_id": JAN, "relation": "ancestors", "depth": 2}, people.relatives, (JAN, "ancestors", 2)),
    ("timeline", {"person_id": JAN}, people.timeline, (JAN,)),
    ("search_documents", {"query": "Josef", "k": 3}, search.search_documents, ("Josef", 3)),
    ("read_document", {"path": "docs/kronika.txt"}, search.read_document, ("docs/kronika.txt", None)),
    ("find_images", {"text": "Anna"}, search.find_images, (None, None, None, None, "Anna")),
    ("find_conflicts", {"person_id": JAN}, conflicts.find_conflicts, (JAN,)),
]


def _call(server, tool, arguments):
    # arguments stay a dict: find_person has its own `name` argument
    return asyncio.run(server.call_tool(tool, arguments))


def test_registers_exactly_the_read_only_tools(tools):
    names = {t.name for t in asyncio.run(build_server(tools).list_tools())}
    assert names == READ_ONLY
    assert not names & {"write_note", "propose_change"}


@pytest.mark.parametrize(("name", "arguments", "fn", "positional"), CASES, ids=[c[0] for c in CASES])
def test_tool_payload_matches_direct_call(tools, name, arguments, fn, positional):
    result = _call(build_server(tools), name, arguments)
    assert result.is_error is False
    assert result.content[0].text == run_tool(tools, fn, *positional).text


def test_descriptions_match_the_agent_tools(tools):
    agent_tools = build_agent(TestModel())._function_toolset.tools
    served = {t.name: t.description for t in asyncio.run(build_server(tools).list_tools())}
    for name in READ_ONLY:
        assert " ".join(served[name].split()) == " ".join(agent_tools[name].description.split())


def test_tool_error_reaches_the_client_as_an_error(tools):
    with pytest.raises(McpToolError, match="find_person"):
        _call(build_server(tools), "get_person", {"person_id": "tree.ged#@I999@"})


class _Down:
    model = "fake"
    dim = 64

    def embed(self, texts):
        raise EmbeddingError("ollama is down")


def test_search_degrades_without_embeddings(tools):
    server = build_server(replace(tools, embedder=_Down()))
    body = json.loads(_call(server, "search_documents", {"query": "Josef"}).content[0].text)
    assert "full-text" in body["note"]
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_mcp_server.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'genea.mcp_server'`).

- [ ] **Step 4: Create `src/genea/mcp_server.py`** (docstrings are copied verbatim from the agent's tool definitions in `agent/core.py`; the parity test enforces that):

```python
"""Read-only MCP server: Claude calls the same query tools the built-in agent uses."""

from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as McpToolError

from genea.agent.prompt import MCP_INSTRUCTIONS
from genea.tool_runner import run_tool
from genea.tools import conflicts, people, search
from genea.tools.base import ToolContext


def build_server(ctx: ToolContext) -> MCPServer:
    """Register the read-only tools. The wrappers are `async def` on purpose: the SDK runs plain
    functions in worker threads, and the SQLite connection in `ctx` belongs to the event-loop thread."""
    server = MCPServer("genea", instructions=MCP_INSTRUCTIONS)

    def run(fn, *args) -> str:
        out = run_tool(ctx, fn, *args)
        if out.is_error:
            raise McpToolError(out.text)
        return out.text

    @server.tool(structured_output=False)
    async def find_person(name: str, born_around: int | None = None, place: str | None = None) -> str:
        """Find people in the family tree by name (accents optional). Returns candidate ids such as
        'tree.ged#@I12@' with birth and death. Call this before any other person tool."""
        return run(people.find_person, name, born_around, place)

    @server.tool(structured_output=False)
    async def get_person(person_id: str) -> str:
        """Full record of one person: names, events with GEDCOM citations, parents, spouses,
        children and family ids."""
        return run(people.get_person, person_id)

    @server.tool(structured_output=False)
    async def relatives(person_id: str,
                        relation: Literal["parents", "children", "siblings", "spouses", "ancestors", "descendants"],
                        depth: int = 1) -> str:
        """Relatives of a person. depth (1-6) is used for ancestors and descendants."""
        return run(people.relatives, person_id, relation, depth)

    @server.tool(structured_output=False)
    async def timeline(person_id: str) -> str:
        """A person's life events in order, with births, marriages and deaths of parents, spouses and children."""
        return run(people.timeline, person_id)

    @server.tool(structured_output=False)
    async def search_documents(query: str, k: int = 8) -> str:
        """Search the text documents (letters, chronicles, registers, notes). Returns passages with file and page."""
        return run(search.search_documents, query, k)

    @server.tool(structured_output=False)
    async def read_document(path: str, page: int | None = None) -> str:
        """Read the full text of a document (or one PDF page) found by search_documents."""
        return run(search.read_document, path, page)

    @server.tool(structured_output=False)
    async def find_images(person: str | None = None, place: str | None = None, year_from: int | None = None,
                          year_to: int | None = None, text: str | None = None) -> str:
        """Find photos by person (an id from find_person, or a name), place, year range or caption text."""
        return run(search.find_images, person, place, year_from, year_to, text)

    @server.tool(structured_output=False)
    async def find_conflicts(person_id: str | None = None) -> str:
        """Check the tree for impossible or suspicious data (events after death, parents too young or
        too old, documents that disagree). Without person_id it checks everyone."""
        return run(conflicts.find_conflicts, person_id)

    return server


def serve(ctx: ToolContext) -> None:
    """Serve on stdio until the client disconnects."""
    build_server(ctx).run("stdio")
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_mcp_server.py -q`
Expected: PASS (12 tests). If `test_descriptions_match_the_agent_tools` fails only on whitespace or a prefix, look at how the SDK derives `description` (it should use the docstring) before changing the docstrings; the two sources must stay identical.

- [ ] **Step 6: Run the whole suite, then commit**

Run: `uv run pytest -q`
Expected: all pass.

```bash
git add pyproject.toml uv.lock src/genea/mcp_server.py tests/test_mcp_server.py
git commit -m "Add the read-only MCP server with the 8 query tools

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `genea mcp` command, startup errors, stdio smoke test

**Files:**
- Create: `src/genea/cli/mcpcmds.py`
- Modify: `src/genea/cli/main.py:6`
- Create: `tests/test_cli_mcp.py`

**Interfaces:**
- Consumes: `serve(ctx)` (Task 3); `common.open_kb`, `common.open_tool_context`, `common.get_embedder`; `common.console` (rebound to a stderr console for the command's duration).
- Produces: CLI command `genea [--kb PATH] mcp`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_cli_mcp.py`:

```python
import asyncio
import os
import sys

import pytest
from typer.testing import CliRunner

from genea.cli.main import app
from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.kb import KnowledgeBase

runner = CliRunner()


def _prepare(sample_root):
    (sample_root / ".genea").mkdir(exist_ok=True)
    (sample_root / ".genea" / "config.toml").write_text('exclude = ["photos/**"]\n', encoding="utf-8")


def test_not_indexed_exits_1_with_message_on_stderr_only(sample_root):
    _prepare(sample_root)
    result = runner.invoke(app, ["--kb", str(sample_root), "mcp"])
    assert result.exit_code == 1
    assert "not indexed yet" in result.stderr
    assert result.stdout == ""


def test_missing_extra_exits_1_with_hint(sample_root, monkeypatch):
    _prepare(sample_root)
    monkeypatch.setitem(sys.modules, "genea.mcp_server", None)  # makes the import fail like a missing extra
    result = runner.invoke(app, ["--kb", str(sample_root), "mcp"])
    assert result.exit_code == 1
    assert "uv sync --extra mcp" in result.stderr
    assert result.stdout == ""


class _IndexEmbedder(FakeEmbedder):
    """Same model name and size as the default settings, so the subprocess can open the index."""

    model = "bge-m3"


def test_stdio_server_end_to_end(sample_root, tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from genea.agent.prompt import MCP_INSTRUCTIONS

    _prepare(sample_root)
    index_kb(KnowledgeBase.open(str(sample_root)), _IndexEmbedder(dim=1024))
    stderr_path = tmp_path / "server-stderr.txt"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from genea.cli.main import app; app()", "--kb", str(sample_root), "mcp"],
        env=dict(os.environ),
    )

    async def session_run():
        with stderr_path.open("w", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    listed = await session.list_tools()
                    found = await session.call_tool("find_person", {"name": "Jan Novák", "born_around": 1900})
                    bad_id = await session.call_tool("get_person", {"person_id": "tree.ged#@I999@"})
                    bad_args = await session.call_tool("relatives", {"person_id": "tree.ged#@I12@", "relation": "cousins"})
                    again = await session.call_tool("get_person", {"person_id": "tree.ged#@I12@"})
                    return init, listed, found, bad_id, bad_args, again

    init, listed, found, bad_id, bad_args, again = asyncio.run(session_run())
    assert init.instructions == MCP_INSTRUCTIONS
    assert {t.name for t in listed.tools} == {
        "find_person", "get_person", "relatives", "timeline", "search_documents",
        "read_document", "find_images", "find_conflicts"}
    assert found.is_error is False and "Jan Novák" in found.content[0].text  # diacritics survive stdio
    assert bad_id.is_error is True and "find_person" in bad_id.content[0].text
    assert bad_args.is_error is True            # invalid arguments are an error, not a crash...
    assert again.is_error is False              # ...and the server keeps serving afterwards
    assert "will be sent" in stderr_path.read_text(encoding="utf-8")  # privacy notice goes to stderr
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cli_mcp.py -q`
Expected: FAIL (no such command `mcp`; exit code 2).

- [ ] **Step 3: Create `src/genea/cli/mcpcmds.py`**:

```python
"""`genea mcp`: serve the knowledge base to Claude over MCP (stdio)."""

from __future__ import annotations

import typer
from rich.console import Console

from genea.cli import common
from genea.cli.app import app


@app.command("mcp")
def mcp_serve(ctx: typer.Context) -> None:
    """Serve the knowledge base to Claude (Code or Desktop) as a read-only MCP server over stdio."""
    err = Console(stderr=True)
    try:
        from genea.mcp_server import serve
    except ImportError as exc:
        err.print("[red]The MCP server needs the optional extra: uv sync --extra mcp[/red]")
        raise typer.Exit(1) from exc
    # stdout carries the protocol, so every message from the shared CLI helpers must go to stderr.
    stdout_console = common.console
    common.console = err
    try:
        kb = common.open_kb(ctx)
        tools = common.open_tool_context(kb)
        if kb.settings.privacy_notice:
            # Rich wraps at 80 columns; `will be sent` stays on the first line, where the test looks for it.
            err.print("[yellow]Note: tool results (your family-tree data) will be sent to the connected "
                      "Claude client. (set privacy_notice = false to hide this)[/yellow]")
        serve(tools)
    finally:
        common.console = stdout_console
```

- [ ] **Step 4: Register the module** — in `src/genea/cli/main.py` change the import line to:

```python
from genea.cli import agentcmds, changecmds, indexcmds, kbcmds, mcpcmds, querycmds  # noqa: E402,F401
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_cli_mcp.py -q`
Expected: PASS (3 tests). If the smoke test hangs or `initialize()` raises, the likely cause is something writing to stdout before the server starts: run `uv run genea --kb <path> mcp < NUL` by hand and look at what appears on stdout. Fix the source of the output (do not filter it in the test).

- [ ] **Step 6: Run the whole suite, then commit**

Run: `uv run pytest -q`
Expected: 250 passed, 2 skipped, 15 deselected (the full suite takes about 3 minutes).

```bash
git add src/genea/cli/mcpcmds.py src/genea/cli/main.py tests/test_cli_mcp.py
git commit -m "Add genea mcp command and stdio end-to-end test

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Docs and spec corrections

**Files:**
- Modify: `README.md` (add a "Use with Claude" section after the cloud-model notes near lines 17-23, and a row for `genea mcp` in the command table if the README has one)
- Modify: `docs/superpowers/specs/2026-10-08-mcp-server-design.md`
- Modify: `docs/superpowers/specs/2026-10-06-genealogy-agent-design.md:20,48,69`

- [ ] **Step 1: README section.** Add:

````markdown
## Use with Claude (MCP)

`genea mcp` serves the knowledge base to Claude Code or Claude Desktop as a **read-only** MCP server
(search, people, relatives, timelines, documents, photos, conflict checks). Notes and proposed changes
still go through the `genea` CLI.

```bash
uv sync --extra mcp
genea --kb <kb> index        # the knowledge base must be indexed first
```

Claude Code:

```bash
claude mcp add genea -- uv run --directory <path to this repo> --extra mcp genea --kb <kb> mcp
```

Claude Desktop (`claude_desktop_config.json`; use absolute paths, and double backslashes on Windows):

```json
{
  "mcpServers": {
    "genea": {
      "command": "uv",
      "args": ["run", "--directory", "C:\\path\\to\\genealogy-rag", "--extra", "mcp", "genea", "--kb", "C:\\path\\to\\kb", "mcp"]
    }
  }
}
```

Ollama is optional here: without it, document search falls back to keywords. Tool results, which contain
your family-tree data, are sent to the Claude client you connect (hide the startup note with
`privacy_notice = false`).
````

- [ ] **Step 2: Spec corrections.**
  - In `2026-10-08-mcp-server-design.md` section 4, replace the sentence "`use_utf8_stdout` is not applied, because the SDK owns the stream." with "The global callback's `use_utf8_stdout()` only sets the stdout encoding to UTF-8 and is harmless here."
  - Same file, sections 2/3: add "Tool wrappers are `async def`: the SDK runs plain functions in worker threads and the SQLite connection is bound to the event-loop thread." and, in section 5, replace the "to be verified" wording on the SDK with the verified facts (class `MCPServer` in `mcp.server.mcpserver`, `mcp>=2.3`), and change `Status:` to `Approved; implemented on jrumanov/claude-mcp`.
  - In `2026-10-06-genealogy-agent-design.md` lines 20, 48, 69: append "(MCP adapter: see `2026-10-08-mcp-server-design.md`)" to the line-20 and line-48 mentions; leave line 69 as is.

- [ ] **Step 3: Verify, manual check, commit.**

Run: `uv run pytest -q`
Expected: all pass.

Manual (not CI; needs a real indexed KB): `claude mcp add genea -- uv run --directory <repo> genea --kb <kb> mcp`, start Claude Code, ask a question that needs `find_person` and `search_documents`, confirm the answer cites `[tree.ged:L…]`. Report the result to the user; if it cannot be run, say so.

```bash
git add README.md docs/superpowers/specs
git commit -m "Document the MCP server and record the verified SDK facts

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-review against the spec

| Spec requirement | Where |
|---|---|
| stdio only, 8 read tools, no write tools | Task 3 (`test_registers_exactly_the_read_only_tools`) |
| Shared `tool_runner`, agent unchanged | Task 1 (full suite stays green; prompt hash in Task 2) |
| Errors returned as tool results, `is_error` | Task 3 (in-process raise) + Task 4 (`is_error` over the wire) |
| `instructions` from a read-only prompt | Task 2, asserted in Task 4 (`init.instructions`) |
| Not indexed / schema mismatch / missing extra, stderr only | Task 4 CLI tests (schema mismatch reuses `open_tool_context`, already covered by existing tests) |
| Ollama down -> keyword search | Task 3 |
| stdout reserved for protocol | Task 4 (`common.console` rebound to stderr; the stdio test records transport faults via a `message_handler` and asserts no non-protocol lines reached the client) |
| Privacy notice on stderr | Task 4 |
| Optional extra, README docs | Tasks 3 and 5 |
| Czech diacritics through stdio | Task 4 smoke test |
| Concurrent clients, output-size tuning | Documented risks only; no code (spec section 7) |
