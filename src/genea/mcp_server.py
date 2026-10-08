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
