"""Run a tool function for a model or an MCP client: failures and oversize results become plain text."""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from pydantic import BaseModel

from genea.kb import KnowledgeBase
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

    The output stays valid JSON followed by TRUNCATION_MARKER (the only text allowed beyond `limit`), with
    `cut_fields` naming the top-level fields that lost text or list items.
    Order of trimming: long strings, then list items, then everything but `sources`; at least one
    source is always kept, even if that alone exceeds a tiny limit.
    """
    sources: list[Any] = []
    for src in data.get("sources", []):
        if src not in sources:
            sources.append(src)
    original = {**data, "sources": sources}
    cut: list[str] = []

    def mark(d: dict[str, Any]) -> dict[str, Any]:
        for key, value in d.items():
            if key not in cut and key != "sources" and value != original.get(key):
                cut.append(key)
        return {**d, "truncated": True, "cut_fields": list(cut)}

    def fits(d: dict[str, Any]) -> bool:
        return len(dump(mark(d))) <= limit

    data = original
    for cap in (2000, 500, 200, 80):
        data = cut_strings(data, cap)
        if fits(data):
            return dump(mark(data))
    lists = [k for k, v in data.items() if k != "sources" and isinstance(v, list)]
    for key in lists:
        while len(data[key]) > 1 and not fits(data):
            data[key] = data[key][: len(data[key]) // 2]
        if fits(data):
            return dump(mark(data))
    cut[:] = [k for k in original if k != "sources"]
    data = {"sources": sources}
    while len(data["sources"]) > 1 and not fits(data):
        data["sources"] = data["sources"][: len(data["sources"]) // 2]
    return dump(mark(data))


def render_limited(result: BaseModel, limit: int) -> str:
    text = result.model_dump_json(exclude_none=True)
    if len(text) <= limit:
        return text
    return shrink(result.model_dump(mode="json", exclude_none=True), limit) + TRUNCATION_MARKER


def render(ctx: ToolContext, result: BaseModel) -> str:
    return render_limited(result, ctx.kb.settings.tool_output_chars)


def error_limited(message: str, limit: int) -> str:
    """A failure is a normal tool result, so repeated failures never exhaust a per-tool retry budget."""
    if len(message) > limit:
        message = message[:limit] + "…"
    return dump({"error": message})


def error_text(ctx: ToolContext, message: str) -> str:
    return error_limited(message, ctx.kb.settings.tool_output_chars)


def run_tool(ctx: ToolContext, fn, *args, **kwargs) -> ToolOutput:
    try:
        return ToolOutput(render(ctx, fn(ctx, *args, **kwargs)), False)
    except ToolError as exc:
        return ToolOutput(error_text(ctx, str(exc)), True)
    except Exception as exc:  # noqa: BLE001 - one bad file or index must not crash the agent loop or server
        return ToolOutput(error_text(ctx, f"{fn.__name__} failed: {type(exc).__name__}: {exc}"), True)


def run_kb_tool(kb: KnowledgeBase, fn, *args, **kwargs) -> ToolOutput:
    """run_tool for tools that need only the knowledge base, not the index (the DNA tools)."""
    limit = kb.settings.tool_output_chars
    try:
        return ToolOutput(render_limited(fn(kb, *args, **kwargs), limit), False)
    except ToolError as exc:
        return ToolOutput(error_limited(str(exc), limit), True)
    except Exception as exc:  # noqa: BLE001 - one bad file must not crash the agent loop or server
        return ToolOutput(error_limited(f"{fn.__name__} failed: {type(exc).__name__}: {exc}", limit), True)
