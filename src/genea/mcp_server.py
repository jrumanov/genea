"""MCP server: the query tools the built-in agent uses, plus analysis tools that write only under .genea/analyses/."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as McpToolError

from genea.agent.prompt import MCP_INSTRUCTIONS
from genea.analysis import api as analysis
from genea.dna import tools as dna_tools
from genea.dna.limits import RsidBudget
from genea.index.db import SchemaMismatch
from genea.index.progress import index_state, read_progress
from genea.index.status import IndexSummary, index_summary
from genea.kb import KnowledgeBase
from genea.tool_runner import run_kb_tool, run_tool
from genea.tools import conflicts, mentions, people, search
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
    """Register the tools. The wrappers are `async def` on purpose: the SDK runs plain
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

    budget = RsidBudget(gate.kb.settings.dna_session_rsids)  # one budget for the whole MCP session

    def run_dna(fn, *args) -> str:
        """DNA tools need no index: they answer before and while `genea index` runs."""
        out = run_kb_tool(gate.kb, fn, *args)
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
    async def documents_about(person_id: str, page: int = 1) -> str:
        """Every text document that mentions a person (an id from find_person), grouped by file with passages to cite, best matches first. Use it when asked for everything about someone; it says how many files there are and whether more pages exist (page=2, ...). Files marked ambiguous may be about a namesake."""
        return run(mentions.documents_about, person_id, page)

    @server.tool(structured_output=False)
    async def search_documents(query: str, k: int = 8, per_file: int = 1) -> str:
        """Search the text documents (letters, chronicles, registers, notes). Returns passages with file and page (or, for recordings, the time in the recording). Returns one passage per file unless per_file is higher; more_files and more_chunks count candidate files and passages not returned (a lower bound, not all of them are matches)."""
        return run(search.search_documents, query, k, per_file)

    @server.tool(structured_output=False)
    async def read_document(path: str, page: int | None = None, start_s: float | None = None) -> str:
        """Read the full text of a document (or one PDF page) found by search_documents. For a recording
        transcript give start_s (seconds) to read from that moment; if next_start_s is returned, call again with it."""
        return run(search.read_document, path, page, start_s)

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
    async def analyze(scope: Literal["person", "family", "branch"], target_id: str,
                      direction: Literal["ancestors", "descendants", "both"] = "both", depth: int = 4) -> str:
        """Check a person, a family (id like 'tree.ged#@F3@') or a branch (ancestors/descendants of a person,
        depth 1-6) for conflicts and gaps. Writes a report under .genea/analyses/ and returns the analysis name,
        counts and the first findings; read_analysis pages through the rest. The tree is not changed."""
        return run(analysis.analyze, scope, target_id, direction, depth)

    @server.tool(structured_output=False)
    async def read_analysis(name: str, severity: Literal["error", "needs_review", "info"] | None = None,
                            page: int = 1) -> str:
        """One page of an analysis' findings and their proposals, optionally only one severity."""
        return run(analysis.read_analysis, name, severity, page)

    @server.tool(structured_output=False)
    async def propose(name: str,
                      operation: Literal["add_event", "update_event", "add_note", "add_source_citation",
                                         "link_child", "add_person"],
                      payload: dict[str, Any], sources: list[str], rationale: str,
                      finding_id: str | None = None) -> str:
        """Record a proposed correction in an analysis, for the user to review; the tree is not changed.
        payload: add_event {person_id, type, date?, place?}; update_event {person_id, line, date?, place?}
        (line from get_person); add_note {person_id, text}; add_source_citation {person_id, source, page?,
        event_type?}; link_child {family_id, child_id} (child_id may be 'proposal:<id>' of an add_person
        proposal); add_person {given, surname, sex?, birth_date?, birth_place?}. sources are citations copied
        from tool results, like '[docs/matrika.pdf p.3]'."""
        return run(analysis.propose, name, operation, payload, sources, rationale, finding_id, "claude")

    @server.tool(structured_output=False)
    async def withdraw(name: str, proposal_id: str) -> str:
        """Withdraw one of the proposals of an analysis."""
        return run(analysis.withdraw, name, proposal_id)

    @server.tool(structured_output=False)
    async def add_analysis_note(name: str, text: str) -> str:
        """Add a research note to an analysis report (kept apart from the evidence)."""
        return run(analysis.add_note, name, text)

    @server.tool(structured_output=False)
    async def build_iteration(name: str) -> str:
        """Write the next iteration of the tree under .genea/analyses/<name>/: a copy with every active proposal
        applied and marked by a GENEA note, plus a diff and a report. The real tree is not changed."""
        return run(analysis.build_iteration, name)

    @server.tool(structured_output=False)
    async def dna_summary(person: str) -> str:
        """What DNA data exists for a person (a DNA person name, or the tree id it was imported for, like
        'tree.ged#@I12@'): inferred sex, SNP count, call rate, warnings and whether haplogroups were analysed."""
        return run_dna(dna_tools.dna_summary, person)

    @server.tool(structured_output=False)
    async def haplogroups(person: str) -> str:
        """The Y (direct paternal line) and mtDNA (direct maternal line) haplogroups computed from a person's
        raw DNA file, with the markers used and the tools that called them. Supporting evidence, never proof of a tree link."""
        return run_dna(dna_tools.haplogroups, person)

    @server.tool(structured_output=False)
    async def snp_lookup(person: str, rsids: list[str]) -> str:
        """Genotypes of 1-20 SNPs by rsID (like rs4988235) from a person's raw DNA file. Limited on purpose:
        at most 20 rsIDs per call and a fixed budget per session. Look up only what the user asks about."""
        return run_dna(dna_tools.snp_lookup, budget, person, rsids)

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
