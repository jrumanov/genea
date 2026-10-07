"""Shared plumbing for tools: context, errors, id parsing."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from genea.index.db import open_index
from genea.index.embed import Embedder
from genea.kb import KnowledgeBase


@dataclass
class ToolContext:
    kb: KnowledgeBase
    conn: sqlite3.Connection
    embedder: Embedder


class ToolError(Exception):
    """Bad tool input. The message is shown to the model (or user) so it can correct itself."""


def open_tools(kb: KnowledgeBase, embedder: Embedder) -> ToolContext:
    return ToolContext(kb, open_index(kb, embedder), embedder)


def person_id(ged_file: str, xref: str) -> str:
    return f"{ged_file}#{xref}"


def _parse(ctx: ToolContext, raw: str, table: str, what: str, hint: str) -> tuple[str, str]:
    raw = (raw or "").strip()
    if "#" in raw:
        ged, xref = raw.split("#", 1)
    else:
        geds = [r[0] for r in ctx.conn.execute(f"SELECT DISTINCT ged_file FROM {table}")]
        if len(geds) != 1:
            raise ToolError(f"Ambiguous {what} id {raw!r}: use '<file>#<xref>' "
                            f"(GEDCOM files: {', '.join(geds) or 'none'})")
        ged, xref = geds[0], raw
    xref = "@" + xref.strip().strip("@") + "@"
    found = ctx.conn.execute(f"SELECT 1 FROM {table} WHERE ged_file = ? AND xref = ?", (ged, xref)).fetchone()
    if found is None:
        raise ToolError(f"No {what} with id {ged}#{xref}. {hint}")
    return ged, xref


def parse_person_id(ctx: ToolContext, raw: str) -> tuple[str, str]:
    return _parse(ctx, raw, "persons", "person", "Use find_person to look up ids.")


def parse_family_id(ctx: ToolContext, raw: str) -> tuple[str, str]:
    return _parse(ctx, raw, "families", "family",
                  "get_person lists a person's family ids (families_as_spouse / families_as_child).")
