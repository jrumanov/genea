"""Viewer page for GEDCOM files: the record around a cited line."""

from __future__ import annotations

import html

from genea.ingest.gedcom.parser import GedDocument, GedLine, parse_gedcom
from genea.kb import KnowledgeBase
from genea.viewer import pages, textview

TOP_LINES = 60


def display(line: GedLine) -> str:
    if line.level < 0:  # blank or unparseable line
        return line.value or ""
    parts = [str(line.level), line.xref, line.tag, line.value]
    return " ".join(p for p in parts if p)


def record_lines(doc: GedDocument, line_no: int) -> list[tuple[int, str]]:
    """The level-0 record containing `line_no`, up to the line before the next level-0 line."""
    start = None
    for i, line in enumerate(doc.lines):
        if line.line_no > line_no:
            break
        if line.level == 0:
            start = i
    if start is None:
        return []
    end = len(doc.lines)
    for i in range(start + 1, len(doc.lines)):
        if doc.lines[i].level == 0:
            end = i
            break
    return [(line.line_no, display(line)) for line in doc.lines[start:end]]


def render_ged(kb: KnowledgeBase, rel: str, params: dict[str, str]) -> str:
    try:
        doc = parse_gedcom(kb.resolve_inside(rel))
    except Exception as exc:
        return pages.notice("Cannot read this file", f"{type(exc).__name__}: {exc}")
    wanted = pages.int_param(params, "line")
    rows = record_lines(doc, wanted) if wanted is not None else []
    note = ""
    if not any(n == wanted for n, _ in rows):
        rows = [(line.line_no, display(line)) for line in doc.lines[:TOP_LINES]]
        if "line" in params:
            note = textview.chunk_note()
    out = []
    for n, text in rows:
        body = html.escape(f"{n:>6}  {text}")
        out.append(f'<mark id="hit">{body}</mark>' if n == wanted else body)
    return pages.shell(rel, pages.header(rel) + note + "<pre>" + "\n".join(out) + "</pre>" + pages.SCROLL)
