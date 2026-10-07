"""Text extraction (txt/md/docx/pdf) and chunking."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import docx
from pypdf import PdfReader

DOC_EXTS = frozenset({".txt", ".md", ".docx", ".pdf"})


@dataclass
class Page:
    number: int | None
    text: str


@dataclass
class DocExtract:
    pages: list[Page] = field(default_factory=list)
    needs_ocr: bool = False


@dataclass
class Chunk:
    page: int | None
    chunk_no: int
    text: str


def _read_text_file(path: Path) -> str:
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1250", errors="replace")


def _single(text: str) -> DocExtract:
    return DocExtract([Page(None, text)] if text.strip() else [])


def extract_document(path: Path) -> DocExtract:
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        return _single(_read_text_file(path))
    if suffix == ".docx":
        document = docx.Document(str(path))
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        return _single("\n".join(p for p in parts if p.strip()))
    if suffix == ".pdf":
        reader = PdfReader(str(path))
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(Page(number, text))
        return DocExtract(pages, needs_ocr=bool(reader.pages) and not pages)
    raise ValueError(f"Unsupported document type: {path.suffix}")


def chunk_pages(pages: list[Page], size: int = 3200, overlap: int = 400) -> list[Chunk]:
    """Split pages into ~size-character chunks (~800 tokens) that overlap by `overlap` characters."""
    chunks: list[Chunk] = []
    for page in pages:
        text = page.text.strip()
        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            if end < len(text):
                cut = text.rfind(" ", start + size - 400, end)
                if cut > start:
                    end = cut
            piece = text[start:end].strip()
            if piece:
                chunks.append(Chunk(page.number, len(chunks), piece))
            if end >= len(text):
                break
            start = max(end - overlap, start + 1)
    return chunks
