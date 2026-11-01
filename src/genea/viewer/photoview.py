"""Viewer page for photos: the picture (when a browser can show it) and its indexed metadata."""

from __future__ import annotations

import html
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

from genea.kb import KnowledgeBase
from genea.viewer import pages
from genea.viewer.regions import image_regions_for, parse_person_param, person_forms, region_matches

NOT_SHOWN = {".heic"}


def _list(value: str | None) -> str:
    try:
        items = json.loads(value or "[]")
    except ValueError:
        return value or ""
    return ", ".join(str(i) for i in items) if isinstance(items, list) else str(items)


def _pct(value: float) -> float:
    return round(value * 100, 4)


def frame(img: str, regions: list[sqlite3.Row], matches: list[bool]) -> str:
    """The picture with one percent-positioned box per region; matching ones are solid, others faint."""
    boxes = "".join(
        f'<div class="{"box" if hit else "box faint"}" style="left:{_pct(r["x"])}%;top:{_pct(r["y"])}%;'
        f'width:{_pct(r["w"])}%;height:{_pct(r["h"])}%" title="{html.escape(r["name"] or "")}"></div>'
        for r, hit in zip(regions, matches))
    return f'<div class="frame">{img}{boxes}</div>'


def _face_overlay(conn: sqlite3.Connection, rel: str, raw_person: str, picture: str) -> str:
    person = parse_person_param(conn, raw_person)
    if person is None:
        return picture
    forms, birth_year = person_forms(conn, *person)
    regions = image_regions_for(conn, rel)
    matches = [region_matches(r["name"], forms, birth_year) for r in regions]
    note = "" if any(matches) else '<p class="note">No face position stored for this person.</p>'
    return frame(picture, regions, matches) + note


def render_photo(kb: KnowledgeBase, conn: sqlite3.Connection, rel: str,
                 params: dict[str, str]) -> str:
    row = conn.execute("SELECT i.* FROM images i JOIN files f ON f.id = i.file_id WHERE f.path = ?",
                       (rel,)).fetchone()
    alt = html.escape(row["title"] or "") if row is not None else ""
    if Path(rel).suffix.lower() in NOT_SHOWN:
        picture = '<p class="note">Your browser cannot show this image format; open the original file instead.</p>'
    else:
        src = "/raw/" + quote(rel, safe="/")
        if Path(rel).suffix.lower() in (".tif", ".tiff"):
            src += f"?page={pages.int_param(params, 'page') or 0}"
        picture = f'<img src="{html.escape(src)}" alt="{alt}" style="max-width:100%">'
        if (pages.int_param(params, "page") or 0) == 0 and params.get("p"):
            picture = _face_overlay(conn, rel, params["p"], picture)
    fields = []
    if row is not None:
        for label, value in (("Title", row["title"]), ("Description", row["description"]),
                             ("Date taken", row["date_taken"]), ("Place", row["place"]),
                             ("Persons shown", _list(row["persons_shown"])),
                             ("Keywords", _list(row["keywords"]))):
            if value:
                fields.append(f"<dt>{label}</dt><dd>{html.escape(str(value))}</dd>")
    return pages.shell(rel, pages.header(rel) + picture + f"<dl>{''.join(fields)}</dl>")
