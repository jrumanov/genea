"""Scanned PDFs (no text layer): which ones still wait for an md, and rendering their pages to images."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from genea.index.db import connect_readonly
from genea.kb import KnowledgeBase


def sibling_md(pdf: Path) -> Path:
    """The md that carries a scanned PDF's text: same folder, same name, `.md`."""
    return pdf.with_suffix(".md")


def unread_scans(kb: KnowledgeBase) -> list[str]:
    """Relative paths of indexed PDFs with no text layer and no sibling md on disk, sorted."""
    if not kb.index_path.exists():
        return []
    try:
        conn = connect_readonly(kb.index_path)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute("SELECT path FROM files WHERE kind = 'doc' AND status = 'needs_ocr' ORDER BY path")
        return [r["path"] for r in rows if not sibling_md(kb.root / r["path"]).exists()]
    except sqlite3.Error:  # a run is creating the schema or holds a write lock
        return []
    finally:
        conn.close()
