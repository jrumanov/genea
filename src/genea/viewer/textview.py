"""Viewer pages for text documents (.md/.txt/.docx) and Whisper transcripts."""

from __future__ import annotations

import html
import sqlite3

from genea.ingest.docs import extract_document, read_transcript
from genea.ingest.transcript import format_time
from genea.kb import KnowledgeBase
from genea.viewer import pages

CHANGED = "The file changed since it was indexed; showing it without highlighting"


def mark_chunk(page_text: str, chunk_text: str) -> str | None:
    """Escaped `page_text` (stripped, as indexed) with the chunk in <mark id="hit">; None if not found."""
    text = page_text.strip()
    at = text.find(chunk_text) if chunk_text else -1
    if at < 0:
        return None
    end = at + len(chunk_text)
    return (html.escape(text[:at]) + '<mark id="hit">' + html.escape(text[at:end])
            + "</mark>" + html.escape(text[end:]))


def chunk_note() -> str:
    return f'<p class="note">{html.escape(CHANGED)}</p>'


def find_chunk(conn: sqlite3.Connection, rel: str, params: dict[str, str]) -> sqlite3.Row | None:
    """The DB chunk named by `?chunk=`, if any."""
    try:
        number = int(params["chunk"])
    except (KeyError, ValueError):
        return None
    return conn.execute(
        "SELECT c.page, c.text, c.start_s, c.end_s, c.kind FROM chunks c JOIN files f ON f.id = c.file_id "
        "WHERE f.path = ? AND c.chunk_no = ?", (rel, number)).fetchone()


def render_text(kb: KnowledgeBase, conn: sqlite3.Connection, rel: str, params: dict[str, str]) -> str:
    try:
        extract = extract_document(kb.resolve_inside(rel))
    except Exception as exc:
        return pages.notice("Cannot read this file", f"{type(exc).__name__}: {exc}")
    chunk = find_chunk(conn, rel, params)
    wanted = int(params["page"]) if params.get("page", "").isdigit() else None
    if all(p.number != wanted for p in extract.pages):
        wanted = None  # a document without that page (plain text has none): show everything
    parts, marked = [], False
    for page in extract.pages:
        if wanted is not None and page.number != wanted:
            continue
        body = None
        if chunk is not None and not marked and chunk["page"] == page.number:
            body = mark_chunk(page.text, chunk["text"])
            marked = body is not None
        if body is None:
            body = html.escape(page.text.strip())
        label = f"<h2>Page {page.number}</h2>" if page.number is not None else ""
        parts.append(f'<div class="page">{label}<pre>{body}</pre></div>')
    note = chunk_note() if "chunk" in params and not marked else ""
    return pages.shell(rel, pages.header(rel) + note + "".join(parts) + pages.SCROLL)


def render_transcript(kb: KnowledgeBase, conn: sqlite3.Connection, rel: str,
                      params: dict[str, str]) -> str | None:
    """The transcript page, or None when the file is not a Whisper transcript."""
    try:
        segments = read_transcript(kb.resolve_inside(rel))
    except Exception:
        return None
    if not segments:
        return None
    chunk = find_chunk(conn, rel, params)
    hit: set[int] = set()
    if chunk is not None and chunk["kind"] != "summary":  # summaries say where to look, never evidence
        eps = 1e-6
        hit = {i for i, s in enumerate(segments)
               if s.start >= chunk["start_s"] - eps and s.end <= chunk["end_s"] + eps}
    note = ""
    if "chunk" in params and chunk is None or (chunk is not None and chunk["kind"] != "summary" and not hit):
        note = chunk_note()
    if not hit and params.get("t", "").isdigit():
        t = int(params["t"])
        first = next((i for i, s in enumerate(segments) if s.end >= t), None)
        if first is not None:
            hit = {first}
    first_hit = min(hit) if hit else None
    rows = []
    for i, s in enumerate(segments):
        line = f'<span class="t">[{format_time(s.start)}]</span> {html.escape(s.text)}'
        if i in hit:
            ident = ' id="hit"' if i == first_hit else ""
            line = f"<mark{ident}>{line}</mark>"
        rows.append(f'<div class="seg">{line}</div>')
    return pages.shell(rel, pages.header(rel) + note + "".join(rows) + pages.SCROLL)
