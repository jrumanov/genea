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


def new_value(operation: str, payload: dict) -> str:
    """One line naming what a proposal adds, for the 'Superseded by' note."""
    if operation == "add_event":
        text = " ".join(x for x in (event_description(payload) or payload["type"], payload.get("date")) if x)
        return text + (f" in {payload['place']}" if payload.get("place") else "")
    text = " ".join(payload["text"].split())
    return text if len(text) <= 80 else text[:79] + "…"


def superseded(operation: str, payload: dict, proposal_id: str, sources: list[SourceRef]) -> str:
    return with_source(f"Superseded by {new_value(operation, payload)} (genea, {proposal_id}).", sources)
