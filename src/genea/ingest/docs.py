"""Text extraction (txt/md/docx/pdf) and chunking."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import docx
from pypdf import PdfReader

from genea.ingest.transcript import Segment, parse_transcript

DOC_EXTS = frozenset({".txt", ".md", ".docx", ".pdf"})


@dataclass
class Page:
    number: int | None
    text: str


@dataclass
class DocExtract:
    pages: list[Page] = field(default_factory=list)
    needs_ocr: bool = False
    segments: list[Segment] = field(default_factory=list)  # a Whisper transcript: timed segments, no pages


@dataclass
class Chunk:
    page: int | None
    chunk_no: int
    text: str
    start_s: float | None = None
    end_s: float | None = None
    kind: str = "text"  # 'summary' for generated summaries


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
    if suffix == ".txt":
        text = _read_text_file(path)
        segments = parse_transcript(text)
        return DocExtract(segments=segments) if segments else _single(text)
    if suffix == ".md":
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


def read_transcript(path: Path) -> list[Segment]:
    """Segments of a Whisper transcript file; [] for any other file."""
    return parse_transcript(_read_text_file(path)) if path.suffix.lower() == ".txt" else []


PAUSE_SECONDS = 3.0  # silence that makes a good place to cut a chunk
PAUSE_WINDOW = 400  # a pause is used for the cut when it comes within this many characters of the size limit


def chunk_segments(segments: list[Segment], size: int = 3200, overlap_segments: int = 2,
                   pause: float = PAUSE_SECONDS) -> list[Chunk]:
    """Chunks of ~`size` characters. A cut prefers a pause of more than `pause` seconds within the last
    PAUSE_WINDOW characters; the next chunk repeats the last `overlap_segments` segments."""
    chunks: list[Chunk] = []
    n = len(segments)
    start = 0
    while start < n:
        length, end, cut = 0, start, None
        while end < n and length < size:
            if end > start and length >= size - PAUSE_WINDOW and segments[end].start - segments[end - 1].end > pause:
                cut = end
            length += len(segments[end].text) + 1
            end += 1
        if end < n and cut is not None:
            end = cut
        piece = segments[start:end]
        chunks.append(Chunk(None, len(chunks), " ".join(s.text for s in piece), piece[0].start, piece[-1].end))
        if end >= n:
            break
        start = max(end - overlap_segments, start + 1)
    return chunks
