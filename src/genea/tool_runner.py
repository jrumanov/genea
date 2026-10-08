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
