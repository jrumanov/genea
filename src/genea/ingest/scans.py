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


class ScanError(Exception):
    """A PDF that cannot be rendered (damaged, password-protected) or a missing renderer."""


def render_pages(pdf: Path, out_dir: Path, dpi: int = 200) -> list[Path]:
    """Render each page of `pdf` to `out_dir/page-001.png`, ... one page at a time; returns the files in order."""
    try:
        import pypdfium2
    except ImportError as exc:
        raise ScanError("pypdfium2 is not installed: run `uv sync --extra scan`") from exc
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    try:
        document = pypdfium2.PdfDocument(str(pdf))
        try:
            for number in range(len(document)):
                page = document[number]
                try:
                    path = out_dir / f"page-{number + 1:03d}.png"
                    page.render(scale=dpi / 72).to_pil().save(path)
                    paths.append(path)
                finally:
                    page.close()
        finally:
            document.close()
    except pypdfium2.PdfiumError as exc:
        raise ScanError(f"cannot read {pdf.name}: {exc}") from exc
    return paths
