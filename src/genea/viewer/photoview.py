"""Viewer page for photos: the picture (when a browser can show it) and its indexed metadata."""

from __future__ import annotations

import html
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

from genea.kb import KnowledgeBase
from genea.viewer import pages

NOT_SHOWN = {".tif", ".tiff", ".heic"}


def _list(value: str | None) -> str:
    try:
        items = json.loads(value or "[]")
    except ValueError:
        return value or ""
    return ", ".join(str(i) for i in items) if isinstance(items, list) else str(items)


def render_photo(kb: KnowledgeBase, conn: sqlite3.Connection, rel: str) -> str:
    row = conn.execute("SELECT i.* FROM images i JOIN files f ON f.id = i.file_id WHERE f.path = ?",
                       (rel,)).fetchone()
    alt = html.escape(row["title"] or "") if row is not None else ""
    if Path(rel).suffix.lower() in NOT_SHOWN:
        picture = '<p class="note">Your browser cannot show this image format; open the original file instead.</p>'
    else:
        picture = f'<img src="{html.escape("/raw/" + quote(rel, safe="/"))}" alt="{alt}" style="max-width:100%">'
    fields = []
    if row is not None:
        for label, value in (("Title", row["title"]), ("Description", row["description"]),
                             ("Date taken", row["date_taken"]), ("Place", row["place"]),
                             ("Persons shown", _list(row["persons_shown"])),
                             ("Keywords", _list(row["keywords"]))):
            if value:
                fields.append(f"<dt>{label}</dt><dd>{html.escape(str(value))}</dd>")
    return pages.shell(rel, pages.header(rel) + picture + f"<dl>{''.join(fields)}</dl>")
