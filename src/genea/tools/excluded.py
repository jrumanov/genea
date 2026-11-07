"""Identity documents that no tool may show, read or search (the user's standing exclusion)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import PurePosixPath

EXCLUDED_TAGS = frozenset({"travel card", "resident permit", "id card", "driving license",
                           "health insurance card"})


def excluded_paths(conn: sqlite3.Connection) -> set[str]:
    """KB-relative paths of tagged images and of the .md files that belong to them."""
    images = [PurePosixPath(row["path"]) for row in conn.execute(
        "SELECT f.path, i.keywords FROM images i JOIN files f ON f.id = i.file_id")
        if EXCLUDED_TAGS & {k.strip().lower() for k in json.loads(row["keywords"])}]
    if not images:
        return set()
    found = {str(p) for p in images}
    for row in conn.execute("SELECT path FROM files WHERE path LIKE '%.md'"):
        md = PurePosixPath(row["path"])
        if any(md.parent == image.parent and (image.stem == md.stem or image.stem.startswith(md.stem + "_"))
               for image in images):
            found.add(row["path"])
    return found
