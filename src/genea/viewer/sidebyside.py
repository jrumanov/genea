"""A scan and its same-stem transcript (.md) shown next to each other."""

from __future__ import annotations

import html
import re
import sqlite3
from pathlib import PurePosixPath
from urllib.parse import quote

from genea.ingest.images import IMAGE_EXTS
from genea.kb import KnowledgeBase
from genea.viewer import pages, photoview, textview
from genea.viewer.server import lookup

SIDE_SUFFIX = re.compile(r"_[A-Za-z]\d{1,2}$")
SCAN_EXTS = tuple(ext for ext in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic") if ext in IMAGE_EXTS)


def _stem(rel: str) -> str:
    return rel[:len(rel) - len(PurePosixPath(rel).suffix)]


def sibling_image(kb: KnowledgeBase, conn: sqlite3.Connection, rel_md: str) -> str | None:
    """The viewable image with the same folder and stem as this .md, if lookup allows it."""
    for ext in SCAN_EXTS:
        for (path,) in conn.execute("SELECT path FROM files WHERE lower(path) = lower(?) ORDER BY path",
                                    (_stem(rel_md) + ext,)).fetchall():
            found = lookup(kb, conn, path)
            if found is not None and found.kind == "image":
                return found.path
    return None


def sibling_md(kb: KnowledgeBase, conn: sqlite3.Connection, rel_image: str) -> str | None:
    """The readable .md with the same stem as this image, if lookup allows it."""
    found = lookup(kb, conn, _stem(rel_image) + ".md")
    return found.path if found is not None and found.kind == "doc" else None


def sides(kb: KnowledgeBase, conn: sqlite3.Connection, rel: str) -> list[str]:
    """Other indexed files of the same document (`x_a1`, `x_a2`, ...), each one lookup allows."""
    match = SIDE_SUFFIX.search(_stem(rel))
    if match is None:
        return []
    base = _stem(rel)[:match.start()]
    found = []
    for (path,) in conn.execute("SELECT path FROM files ORDER BY path"):
        stem = _stem(path)
        other = SIDE_SUFFIX.search(stem)
        if path == rel or other is None or stem[:other.start()] != base:
            continue
        if lookup(kb, conn, path) is not None:
            found.append(path)
    return found


def _side_links(kb: KnowledgeBase, conn: sqlite3.Connection, rel: str) -> str:
    links = "".join(f'<li><a href="{html.escape("/v/" + quote(p, safe="/"))}">{html.escape(p)}</a></li>'
                    for p in sides(kb, conn, rel))
    return f"<ul>{links}</ul>" if links else ""


def render_pair(kb: KnowledgeBase, conn: sqlite3.Connection, rel: str,
                params: dict[str, str]) -> str | None:
    """The two-pane page for an .md or an image that has a partner, else None (the plain page applies)."""
    if rel.lower().endswith(".md"):
        image = sibling_image(kb, conn, rel)
        if image is None:
            return None
        row = conn.execute("SELECT i.title FROM images i JOIN files f ON f.id = i.file_id WHERE f.path = ?",
                           (image,)).fetchone()
        alt = html.escape(row["title"] or "") if row is not None else ""
        left = photoview.picture_html(conn, image, {}, alt)
        right = textview.text_body(kb, conn, rel, params)
    else:
        md = sibling_md(kb, conn, rel)
        if md is None:
            return None
        left = photoview.photo_body(conn, rel, params)
        right = textview.text_body(kb, conn, md, {})
    return pages.shell(rel, pages.two_pane(left + _side_links(kb, conn, rel), right), wide=True)
