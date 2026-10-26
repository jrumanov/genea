"""Pages of the viewer (placeholder until the real renderers land)."""

from __future__ import annotations

import sqlite3

from genea.kb import KnowledgeBase


def render(kb: KnowledgeBase, conn: sqlite3.Connection, rel: str,
           params: dict[str, list[str]]) -> bytes:
    return b"viewer"
