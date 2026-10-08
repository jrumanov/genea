"""Read-only MCP server: Claude calls the same query tools the built-in agent uses."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as McpToolError

from genea.agent.prompt import MCP_INSTRUCTIONS
from genea.index.db import SchemaMismatch
from genea.index.progress import index_state, read_progress
from genea.index.status import IndexSummary, index_summary
from genea.kb import KnowledgeBase
from genea.tool_runner import run_tool
from genea.tools import conflicts, people, search
from genea.tools.base import ToolContext


class IndexGate:
    """Opens the index on the first tool call, so the server starts (and can explain) before the
    knowledge base is indexed or while `genea index` is still running."""

    def __init__(self, kb: KnowledgeBase, open_context: Callable[[], ToolContext]) -> None:
        self.kb = kb
        self._open = open_context
        self._ctx: ToolContext | None = None

    @classmethod
    def ready(cls, ctx: ToolContext) -> IndexGate:
        gate = cls(ctx.kb, lambda: ctx)
        gate._ctx = ctx
        return gate

    def context(self) -> ToolContext:
        if self._ctx is not None:
            return self._ctx
        if not self.kb.index_path.exists():
            if index_state(self.kb) == "building":
                progress = read_progress(self.kb)
                detail = f": {progress.describe()}" if progress else ""
                raise McpToolError(f"The index is being built{detail}. There is nothing to search yet; "
                                   "try again later (index_status shows progress).")
            raise McpToolError(f'The knowledge base is not indexed yet. Run `genea --kb "{self.kb.root}" '
                               "index`, then try again.")
        try:
            self._ctx = self._open()
        except SchemaMismatch as exc:
            raise McpToolError(str(exc)) from exc
        except sqlite3.Error as exc:  # e.g. the indexer is creating the database right now
            raise McpToolError(f"The index cannot be read right now ({exc}). Try again in a moment.") from exc
        return self._ctx

    def note(self) -> str | None:
        """A warning for results from a partial index; None when the index is complete."""
        state = index_state(self.kb)
        if state == "building":
            progress = read_progress(self.kb)
            detail = f" ({progress.describe()})" if progress else ""
            return f"Note: the index is still being built{detail}; these results may be incomplete."
        if state == "interrupted":
            return ("Note: the last indexing run did not finish; these results may be incomplete "
                    "until `genea index` is run again.")
        return None


def status_text(summary: IndexSummary) -> str:
    lines = [f"State: {summary.state}", summary.headline()]
    if summary.counts is not None:
        lines.append("Files: " + ", ".join(f"{kind} {status} {n}" for kind, status, n in summary.counts))
        lines.append(f"People: {summary.people}")
        if summary.problems:
            lines.append(f"Files with problems: {len(summary.problems)} (`genea status` lists them)")
    return "\n".join(lines)


def build_server(source: ToolContext | IndexGate) -> MCPServer:
    """Register the read-only tools. The wrappers are `async def` on purpose: the SDK runs plain
    functions in worker threads, and the SQLite connection in the context belongs to the event-loop thread."""
    gate = source if isinstance(source, IndexGate) else IndexGate.ready(source)
    startup = index_summary(gate.kb).headline()
    server = MCPServer("genea", instructions=f"{MCP_INSTRUCTIONS}Index state when this session started: {startup}\n")

    def run(fn, *args) -> str:
        out = run_tool(gate.context(), fn, *args)
        if out.is_error:
            raise McpToolError(out.text)
        note = gate.note()
        return out.text if note is None else f"{out.text}\n\n{note}"

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

    @server.tool(structured_output=False)
    async def index_status() -> str:
        """Whether the knowledge-base index is complete, still being built (with progress and ETA) or
        incomplete, with file and people counts. Call it when another tool says the index is missing
        or incomplete, or when the user asks about indexing."""
        return status_text(index_summary(gate.kb))

    return server


def serve(gate: IndexGate) -> None:
    """Serve on stdio until the client disconnects."""
    build_server(gate).run("stdio")
