"""Curator tools: research notes (written directly) and change proposals (reviewed by the user)."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from genea.changes.store import ChangeError, ChangeStore
from genea.text import normalize
from genea.tools.base import ToolContext, ToolError
from genea.tools.models import ChangeResult, NoteResult, SourceRef


def write_note(ctx: ToolContext, title: str, markdown: str,
               related_persons: list[str] | None = None) -> NoteResult:
    if not title.strip() or not markdown.strip():
        raise ToolError("A note needs a title and some Markdown content.")
    slug = "-".join(normalize(title).split())[:60] or "note"
    stem = f"{date.today().isoformat()}-{slug}"
    path = ctx.kb.notes_dir / f"{stem}.md"
    n = 2
    while path.exists():
        path = ctx.kb.notes_dir / f"{stem}-{n}.md"
        n += 1
    front = ["---", f"title: {json.dumps(title.strip(), ensure_ascii=False)}",
             f"created: {datetime.now().isoformat(timespec='seconds')}",
             f"related_persons: {json.dumps(related_persons or [], ensure_ascii=False)}", "---", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(front) + markdown.strip() + "\n", encoding="utf-8")
    return NoteResult(path=ctx.kb.rel(path))


def propose_change(ctx: ToolContext, target_kind: str, target_path: str, operation: str,
                   payload: dict[str, Any], rationale: str,
                   sources: list[SourceRef] | None = None) -> ChangeResult:
    try:
        change, created = ChangeStore(ctx.kb).propose(target_kind, target_path, operation, payload,
                                                      rationale, sources or [])
    except ChangeError as exc:
        raise ToolError(str(exc)) from exc
    if created:
        message = (f"Proposed as {change.id}. Nothing has changed yet: the user reviews it with "
                   f"`genea changes show {change.id}` and applies it with `genea changes apply {change.id}`.")
    else:
        message = f"An identical change already exists ({change.id}, {change.status}); not proposed again."
    return ChangeResult(id=change.id, status=change.status, created=created, message=message,
                        sources=change.sources)
