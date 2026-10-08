"""What is indexed and whether a run is active: shared by `genea status` and the MCP `index_status` tool."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from genea.index.db import connect_readonly
from genea.index.progress import IndexProgress, IndexState, index_state, read_progress
from genea.kb import KnowledgeBase


@dataclass
class IndexSummary:
    state: IndexState
    progress: IndexProgress | None = None  # only while building
    counts: list[tuple[str, str, int]] | None = None  # (kind, status, files); None without a readable index
    people: int | None = None
    problems: list[tuple[str, str, str]] = field(default_factory=list)  # (path, status, message)

    def headline(self) -> str:
        if self.state == "missing":
            return "The knowledge base is not indexed yet. Run `genea index`."
        if self.state == "building":
            detail = f": {self.progress.describe()}" if self.progress else "."
            return f"Indexing in progress{detail}"
        if self.state == "interrupted":
            return "The last indexing run did not finish, so the index is incomplete. Run `genea index`."
        return "The index is complete."


def index_summary(kb: KnowledgeBase) -> IndexSummary:
    state = index_state(kb)
    summary = IndexSummary(state, progress=read_progress(kb) if state == "building" else None)
    if not kb.index_path.exists():
        return summary
    try:
        conn = connect_readonly(kb.index_path)
    except sqlite3.Error:
        return summary
    try:
        summary.counts = [(r["kind"], r["status"], r["n"]) for r in conn.execute(
            "SELECT kind, status, COUNT(*) AS n FROM files GROUP BY kind, status ORDER BY kind, status")]
        summary.problems = [(r["path"], r["status"], r["error_msg"] or "") for r in conn.execute(
            "SELECT path, status, error_msg FROM files WHERE status != 'ok' OR error_msg IS NOT NULL ORDER BY path")]
        summary.people = conn.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
    except sqlite3.Error:  # a run is creating the schema or holds a write lock: report what we know
        summary.counts, summary.problems, summary.people = None, [], None
    finally:
        conn.close()
    return summary
