"""The review bundle: one Markdown file per analysis, edited in a PR, read back by apply.

Only two edits mean something: deleting an item's section drops it, and the `decision:` line of a conflict.
"""

from __future__ import annotations

import re

from genea.analysis.models import Analysis, Proposal
from genea.analysis.sourcenote import with_source
from genea.changes.export import describe_operation
from genea.tools.base import ToolError

SECTIONS = ("Events", "Residences", "Notes", "Conflicts", "Leads", "Other")
DECISIONS = ("keep", "replace", "both", "undecided")
_ITEM = re.compile(r"^### (p-\d+): ")
_DECISION = re.compile(r"^decision:\s*(.*?)\s*$", re.IGNORECASE)

INTRO = """To drop an item, delete its whole section (from its `###` line to the next `###` or `##` line) and commit.
To decide a conflict, edit its `decision:` line to `keep` (leave the tree as it is), `replace` (add the new value
and mark the old one) or `both`. Nothing else in this file is read."""


def _section(p: Proposal) -> str:
    if p.conflict is not None:
        return "Conflicts"
    if p.operation == "add_event":
        return "Residences" if p.payload["type"] == "RESI" else "Events"
    if p.operation == "add_note":
        return "Leads" if p.payload["text"].lstrip().startswith("Leads:") else "Notes"
    return "Other"


def _safe(text: str) -> list[str]:
    """Body lines that cannot be mistaken for a header or a decision when the file is read back."""
    return [("\\" + line) if line.startswith("#") or _DECISION.match(line) else line for line in text.splitlines()]


def _item(p: Proposal) -> list[str]:
    lines = [f"### {p.id}: " + " ".join(describe_operation(p.operation, p.payload).split()), ""]
    if p.operation == "add_note":
        lines += _safe(with_source(p.payload["text"], p.sources)) + [""]
    elif p.operation == "add_event" and p.payload.get("note"):
        lines += _safe(with_source(p.payload["note"], p.sources)) + [""]
    lines.append("Why: " + " ".join(p.rationale.split()))
    lines.append("Sources: " + "; ".join(s.cite() for s in p.sources))
    if p.conflict is not None:
        lines += [f"Existing: {p.conflict.existing}", f"decision: {p.conflict.decision}"]
    return lines + [""]


def render_bundle(analysis: Analysis) -> str:
    active = [p for p in analysis.proposals if p.status == "active"]
    out = [f"# Genea review: {analysis.name}", "", *INTRO.splitlines(), ""]
    for section in SECTIONS:
        items = [p for p in active if _section(p) == section]
        if items:
            out += [f"## {section}", ""]
            for p in items:
                out += _item(p)
    return "\n".join(out)


def parse_bundle(text: str, analysis: Analysis) -> tuple[set[str], dict[str, str]]:
    """The ids of the items left in the file and the decision written for each conflict."""
    active = {p.id: p for p in analysis.proposals if p.status != "withdrawn"}  # written ones keep their section
    kept: set[str] = set()
    decisions: dict[str, str] = {}
    current: Proposal | None = None
    for n, line in enumerate(text.splitlines(), start=1):
        line = line.lstrip("﻿") if n == 1 else line
        if line.startswith("#"):
            if n == 1 and line.startswith("# "):
                continue
            if match := _ITEM.match(line):
                pid = match.group(1)
                if pid not in active:
                    raise ToolError(f"line {n}: {pid} is not a proposal of this analysis.")
                if pid in kept:
                    raise ToolError(f"line {n}: {pid} appears twice.")
                kept.add(pid)
                current = active[pid]
            elif re.fullmatch(r"## (" + "|".join(SECTIONS) + ")", line):
                current = None
            else:
                raise ToolError(f"line {n}: {line[:60]!r} is not a section or item header; keep the headers as "
                                "they were.")
        elif match := _DECISION.match(line):
            word = match.group(1).lower()
            if current is None or current.conflict is None:
                raise ToolError(f"line {n}: this item has no conflict to decide.")
            if word not in DECISIONS:
                raise ToolError(f"line {n}: decision {match.group(1)!r} must be keep, replace, both or undecided.")
            decisions[current.id] = word
    return kept, decisions
