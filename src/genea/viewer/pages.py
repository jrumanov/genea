"""Shared HTML shell and the dispatcher that picks a renderer for a cited file."""

from __future__ import annotations

import html
import sqlite3
from pathlib import Path
from urllib.parse import quote

from genea.kb import KnowledgeBase

STYLE = """
:root { --bg: #fdfcf9; --fg: #222; --muted: #666; --mark: #ffe58a; --note: #fff3cd; --rule: #ddd; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #1b1b1d; --fg: #e4e4e4; --muted: #999; --mark: #6b5a00; --note: #3d3410; --rule: #444; }
}
body { background: var(--bg); color: var(--fg); font: 16px/1.55 system-ui, sans-serif;
       max-width: 46rem; margin: 0 auto; padding: 1rem; }
h1 { font-size: 1.1rem; word-break: break-all; }
a { color: inherit; }
pre { white-space: pre-wrap; word-wrap: break-word; font: inherit; }
mark { background: var(--mark); color: inherit; }
.note { background: var(--note); padding: .5rem .75rem; border-radius: 4px; }
.page { border-top: 1px solid var(--rule); margin-top: 1rem; padding-top: .5rem; }
.page h2 { font-size: .9rem; color: var(--muted); }
.seg { margin: .25rem 0; }
.seg .t { color: var(--muted); font-variant-numeric: tabular-nums; }
"""

SCROLL = "<script>document.getElementById('hit')?.scrollIntoView({block:'center'})</script>"


def shell(title: str, body: str) -> str:
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width, initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{STYLE}</style></head>"
            f"<body>{body}</body></html>")


def notice(title: str, message: str) -> str:
    return shell(title, f"<h1>{html.escape(title)}</h1><p class=note>{html.escape(message)}</p>")


def header(rel: str) -> str:
    raw = html.escape("/raw/" + quote(rel, safe="/"))
    return f'<h1>{html.escape(rel)} · <a href="{raw}">original file</a></h1>'


def render(kb: KnowledgeBase, conn: sqlite3.Connection, rel: str, params: dict[str, str]) -> str:
    from genea.viewer import textview
    row = conn.execute("SELECT kind FROM files WHERE path = ?", (rel,)).fetchone()
    if row is None:
        return notice("Not found", "This file is not available in the viewer.")
    if row["kind"] == "doc":
        if Path(rel).suffix.lower() == ".pdf":
            from genea.viewer import pdfview
            return pdfview.render_pdf(kb, conn, rel, params)
        if Path(rel).suffix.lower() == ".txt":
            page = textview.render_transcript(kb, conn, rel, params)
            if page is not None:
                return page
        return textview.render_text(kb, conn, rel, params)
    if row["kind"] == "ged":
        from genea.viewer import gedview
        return gedview.render_ged(kb, rel, params)
    if row["kind"] == "image":
        from genea.viewer import photoview
        return photoview.render_photo(kb, conn, rel)
    return notice("Not supported yet", "This kind of file has no viewer page yet.")
