"""A note's source is text at its end, not a Gramps citation; re-runs compare notes with spacing ignored."""

from __future__ import annotations

from genea.tools.models import SourceRef


def with_source(text: str, sources: list[SourceRef]) -> str:
    return text.rstrip() + "\n\nSource: " + "; ".join(s.cite() for s in sources)


def same_note(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def event_description(payload: dict) -> str | None:
    """The event's description: a residence carries its address, any other event its optional description."""
    if payload.get("address"):
        return f"Address: {payload['address']}"
    return payload.get("description") or None
