"""Append every question to <kb>/.genea/prompts.md so the log can later be read, or turned into evals.

One entry per question: a `## ` header, a yaml block (the machine-readable part; fill in `verdict`
and `note` by hand), the question, and the answer as a blockquote so its own headings never look
like the start of another entry."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime

from genea.kb import KnowledgeBase

# [tree.ged:L7], [docs/matrika.pdf p.3], [photos/x.jpg]
_CITATION = re.compile(r"\[[^\[\]\s][^\[\]]*(?::L\d+|\sp\.\d+|\.(?:jpe?g|png|tiff?))\]", re.IGNORECASE)


def record(kb: KnowledgeBase, *, session: str, turn: int, question: str, answer: str | None = None,
           error: str | None = None, tool_calls: Sequence[str] = (), duration_s: float = 0.0) -> None:
    """Append one entry. Raises OSError when the file cannot be written; does nothing when
    `log_prompts` is off."""
    if not kb.settings.log_prompts:
        return
    citations = len(_CITATION.findall(answer)) if answer else 0
    lines = [
        f"## {datetime.now():%Y-%m-%dT%H:%M:%S} · session {session} · turn {turn}"
        + (" (follow-up)" if turn > 1 else ""),
        "",
        "```yaml",
        f"id: {session}-{turn}",
        f"session: {session}",
        f"turn: {turn}",
        f"followup: {str(turn > 1).lower()}",
        f"model: {kb.settings.model}",
        f"tools: [{', '.join(tool_calls)}]",
        f"tool_calls: {len(tool_calls)}",
        f"citations: {citations}",
        f"duration_s: {duration_s:.1f}",
        f"failed: {str(error is not None).lower()}",
        "verdict:",
        "note:",
        "```",
        "",
        f"**Q:** {question}",
        "",
    ]
    body = f"FAILED: {error}" if error is not None else (answer or "")
    lines += ["**A:**", ""] + [f"> {line}".rstrip() for line in body.splitlines()] + ["", ""]
    path = kb.meta_dir / "prompts.md"
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
