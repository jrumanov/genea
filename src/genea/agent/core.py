"""The PydanticAI agent: tools wired to the model, plus a safe `ask` wrapper."""

from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ToolCallPart
from pydantic_ai.usage import UsageLimits

from genea.agent.prompt import SYSTEM_PROMPT
from genea.kb import KnowledgeBase
from genea.tool_runner import run_tool
from genea.tools import conflicts, curate, people, search
from genea.tools.base import ToolContext
from genea.tools.models import SourceRef


class AgentFailed(RuntimeError):
    """The agent run failed (model unreachable, limits exceeded, invalid output...)."""


@dataclass
class Answer:
    text: str
    tool_calls: list[tuple[str, str]]
    messages: list[ModelMessage]


def _call(ctx: RunContext[ToolContext], fn, *args, **kwargs) -> str:
    return run_tool(ctx.deps, fn, *args, **kwargs).text


def build_agent(model) -> Agent[ToolContext, str]:
    agent = Agent(model, deps_type=ToolContext, instructions=SYSTEM_PROMPT, retries=2, defer_model_check=True)

    @agent.tool
    async def find_person(ctx: RunContext[ToolContext], name: str, born_around: int | None = None,
                    place: str | None = None) -> str:
        """Find people in the family tree by name (accents optional). Returns candidate ids such as
        'tree.ged#@I12@' with birth and death. Call this before any other person tool."""
        return _call(ctx, people.find_person, name, born_around, place)

    @agent.tool
    async def get_person(ctx: RunContext[ToolContext], person_id: str) -> str:
        """Full record of one person: names, events with GEDCOM citations, parents, spouses,
        children and family ids."""
        return _call(ctx, people.get_person, person_id)

    @agent.tool
    async def relatives(ctx: RunContext[ToolContext], person_id: str,
                  relation: Literal["parents", "children", "siblings", "spouses", "ancestors", "descendants"],
                  depth: int = 1) -> str:
        """Relatives of a person. depth (1-6) is used for ancestors and descendants."""
        return _call(ctx, people.relatives, person_id, relation, depth)

    @agent.tool
    async def timeline(ctx: RunContext[ToolContext], person_id: str) -> str:
        """A person's life events in order, with births, marriages and deaths of parents, spouses and children."""
        return _call(ctx, people.timeline, person_id)

    @agent.tool
    async def search_documents(ctx: RunContext[ToolContext], query: str, k: int = 8) -> str:
        """Search the text documents (letters, chronicles, registers, notes). Returns passages with file and page."""
        return _call(ctx, search.search_documents, query, k)

    @agent.tool
    async def read_document(ctx: RunContext[ToolContext], path: str, page: int | None = None) -> str:
        """Read the full text of a document (or one PDF page) found by search_documents."""
        return _call(ctx, search.read_document, path, page)

    @agent.tool
    async def find_images(ctx: RunContext[ToolContext], person: str | None = None, place: str | None = None,
                    year_from: int | None = None, year_to: int | None = None, text: str | None = None) -> str:
        """Find photos by person (an id from find_person, or a name), place, year range or caption text."""
        return _call(ctx, search.find_images, person, place, year_from, year_to, text)

    @agent.tool
    async def find_conflicts(ctx: RunContext[ToolContext], person_id: str | None = None) -> str:
        """Check the tree for impossible or suspicious data (events after death, parents too young or
        too old, documents that disagree). Without person_id it checks everyone."""
        return _call(ctx, conflicts.find_conflicts, person_id)

    @agent.tool
    async def write_note(ctx: RunContext[ToolContext], title: str, markdown: str,
                   related_persons: list[str] | None = None) -> str:
        """Save a research note (Markdown) in the knowledge base's notes folder. Use it only when the
        user asks to keep findings."""
        return _call(ctx, curate.write_note, title, markdown, related_persons)

    @agent.tool
    async def propose_change(ctx: RunContext[ToolContext], target_kind: Literal["image_xmp", "gedcom"],
                       target_path: str, operation: str, payload: dict[str, Any], rationale: str,
                       sources: list[SourceRef] | None = None) -> str:
        """Propose a change for the user to review; nothing changes until they apply it.
        image_xmp (target_path = the photo): operation set_fields with payload {field: value or [values]}
        using XMP-dc:Title, XMP-dc:Description, XMP-dc:Subject, XMP-iptcExt:PersonInImage,
        XMP-photoshop:City, XMP-photoshop:Country, XMP-photoshop:DateCreated, XMP-iptcCore:Location;
        or add_keywords / remove_keywords with payload {"keywords": [...]}.
        gedcom (target_path = the .ged file): add_event {person_id, type, date?, place?},
        update_event {person_id, line, date?, place?}, add_note {person_id, text},
        add_source_citation {person_id, source, page?, event_type?}, link_child {family_id, child_id},
        add_person {given, surname, sex?, birth_date?, birth_place?}.
        Always give a rationale and the sources that support the change."""
        return _call(ctx, curate.propose_change, target_kind, target_path, operation, payload,
                     rationale, sources)

    return agent


def _save_failure(kb: KnowledgeBase, question: str, exc: Exception) -> str:
    path = kb.logs_dir / f"{datetime.now():%Y%m%dT%H%M%S%f}-failure.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"question": question, "error": f"{type(exc).__name__}: {exc}",
                                "traceback": traceback.format_exc()}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return kb.rel(path)


def ask(agent: Agent[ToolContext, str], deps: ToolContext, question: str,
        history: list[ModelMessage] | None = None, runner: asyncio.Runner | None = None) -> Answer:
    """Run one question. A chat session must pass one `runner` for all its questions: the model's
    HTTP client keeps keep-alive connections bound to the event loop that opened them, so a new
    loop per question breaks the second question. Without a runner a one-shot loop is used."""
    settings = deps.kb.settings
    limits = UsageLimits(request_limit=settings.max_tool_calls + 5, tool_calls_limit=settings.max_tool_calls)
    own_runner = runner is None
    if own_runner:
        runner = asyncio.Runner()
    try:
        result = runner.run(agent.run(question, deps=deps, message_history=history, usage_limits=limits))
    except Exception as exc:  # noqa: BLE001 - every failure is reported the same way
        log = _save_failure(deps.kb, question, exc)
        raise AgentFailed(f"{type(exc).__name__}: {exc} (details in {log})") from exc
    finally:
        if own_runner:
            runner.close()
    calls = [(p.tool_name, p.args_as_json_str())
             for m in result.new_messages() for p in m.parts if isinstance(p, ToolCallPart)]
    return Answer(result.output, calls, result.all_messages())
