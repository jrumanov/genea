"""Viewer page for PDFs: the browser's own PDF viewer above the extracted text of the cited page."""

from __future__ import annotations

import html
import sqlite3
from urllib.parse import quote

from genea.ingest.docs import extract_document
from genea.kb import KnowledgeBase
from genea.viewer import pages, textview

NO_TEXT = "No text layer: this PDF has not been read as text yet"


def render_pdf(kb: KnowledgeBase, conn: sqlite3.Connection, rel: str, params: dict[str, str]) -> str:
    try:
        extract = extract_document(kb.resolve_inside(rel))
    except Exception as exc:
        return pages.notice("Cannot read this file", f"{type(exc).__name__}: {exc}")
    chunk = textview.find_chunk(conn, rel, params)
    wanted = int(params["page"]) if params.get("page", "").isdigit() else None
    if wanted is None and chunk is not None:
        wanted = chunk["page"]
    src = "/raw/" + quote(rel, safe="/") + (f"#page={wanted}" if wanted else "")
    frame = f'<iframe src="{html.escape(src)}" style="width:100%;height:70vh;border:1px solid var(--rule)"></iframe>'
    if not extract.pages:
        return pages.shell(rel, pages.header(rel) + frame + f'<p class="note">{html.escape(NO_TEXT)}</p>')
    parts, marked = [], False
    for page in extract.pages:
        if wanted is not None and page.number != wanted:
            continue
        body = None
        if chunk is not None and not marked and chunk["page"] == page.number:
            body = textview.mark_chunk(page.text, chunk["text"])
            marked = body is not None
        if body is None:
            body = html.escape(page.text.strip())
        parts.append(f'<div class="page"><h2>Page {page.number}</h2><pre>{body}</pre></div>')
    note = textview.chunk_note() if "chunk" in params and not marked else ""
    return pages.shell(rel, pages.header(rel) + frame + note + "".join(parts) + pages.SCROLL)
