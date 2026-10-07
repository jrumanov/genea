"""Document and image search tools."""

from __future__ import annotations

import json
import re

import sqlite_vec

from genea.index.embed import EmbeddingError
from genea.ingest.docs import DOC_EXTS, extract_document
from genea.text import normalize
from genea.tools.base import ToolContext, ToolError, parse_person_id
from genea.tools.models import DocHit, DocumentText, ImageOut, ImageResult, SearchResult, SourceRef

RRF_K = 60
CANDIDATES = 30


def _fts_query(text: str) -> str | None:
    tokens = re.findall(r"\w+", text)
    return " OR ".join(f'"{t}"' for t in tokens) or None


def search_documents(ctx: ToolContext, query: str, k: int = 8) -> SearchResult:
    """Hybrid search: FTS5 (BM25) + vector KNN, merged with reciprocal rank fusion."""
    if not query.strip():
        raise ToolError("Give a search query.")
    k = max(1, min(k, 20))
    scores: dict[int, float] = {}
    fts = _fts_query(query)
    if fts:
        rows = ctx.conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?",
            (fts, CANDIDATES)).fetchall()
        for rank, row in enumerate(rows, start=1):
            scores[row[0]] = scores.get(row[0], 0.0) + 1 / (RRF_K + rank)
    note = None
    try:
        vector = ctx.embedder.embed([query])[0]
        rows = ctx.conn.execute(
            "SELECT chunk_id FROM chunks_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (sqlite_vec.serialize_float32(vector), CANDIDATES)).fetchall()
        for rank, row in enumerate(rows, start=1):
            scores[row[0]] = scores.get(row[0], 0.0) + 1 / (RRF_K + rank)
    except EmbeddingError as exc:
        note = f"semantic search unavailable, used full-text search only: {exc}"
    hits = []
    for chunk_id, score in sorted(scores.items(), key=lambda item: -item[1])[:k]:
        row = ctx.conn.execute(
            "SELECT c.page, c.chunk_no, c.text, f.path FROM chunks c JOIN files f ON f.id = c.file_id "
            "WHERE c.id = ?", (chunk_id,)).fetchone()
        source = SourceRef(file=row["path"], page=row["page"], chunk=row["chunk_no"])
        hits.append(DocHit(file=row["path"], page=row["page"], chunk=row["chunk_no"],
                           text=row["text"][:1500], score=round(score, 4), source=source))
    return SearchResult(query=query, hits=hits, note=note, sources=[h.source for h in hits])


def read_document(ctx: ToolContext, path: str, page: int | None = None) -> DocumentText:
    try:
        full_path = ctx.kb.resolve_inside(path)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    if not full_path.is_file() or full_path.suffix.lower() not in DOC_EXTS:
        raise ToolError(f"Not a readable document: {path} (use search_documents to find documents)")
    extract = extract_document(full_path)
    pages = extract.pages if page is None else [p for p in extract.pages if p.number == page]
    if page is not None and not pages:
        raise ToolError(f"Page {page} was not found or has no text in {path}")
    text = "\n\n".join((f"[page {p.number}]\n" if p.number else "") + p.text for p in pages)
    limit = ctx.kb.settings.tool_output_chars
    truncated = len(text) > limit
    if truncated:
        text = text[:limit] + "\n…[truncated]"
    rel = ctx.kb.rel(full_path)
    return DocumentText(file=rel, page=page, text=text, truncated=truncated,
                        sources=[SourceRef(file=rel, page=page)])


def find_images(ctx: ToolContext, person: str | None = None, place: str | None = None,
                year_from: int | None = None, year_to: int | None = None,
                text: str | None = None, limit: int = 20) -> ImageResult:
    where: list[str] = []
    params: list = []
    if person:
        if "#" in person or person.strip().startswith("@"):
            ged, xref = parse_person_id(ctx, person)
            where.append("i.id IN (SELECT source_id FROM links WHERE source_kind = 'image' "
                         "AND person_ged_file = ? AND person_xref = ?)")
            params += [ged, xref]
        else:
            text = f"{person} {text or ''}"
    for value in (place, text):
        for token in normalize(value or "").split():
            where.append("i.search_norm LIKE ?")
            params.append(f"%{token}%")
    if year_from is not None:
        where.append("CAST(substr(i.date_taken, 1, 4) AS INTEGER) >= ?")
        params.append(year_from)
    if year_to is not None:
        where.append("CAST(substr(i.date_taken, 1, 4) AS INTEGER) <= ?")
        params.append(year_to)
    sql = ("SELECT i.*, f.path FROM images i JOIN files f ON f.id = i.file_id"
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY i.date_taken, f.path LIMIT ?")
    rows = ctx.conn.execute(sql, [*params, max(1, min(limit, 100))]).fetchall()
    images = [ImageOut(
        file=r["path"], sidecar=r["sidecar_path"], title=r["title"], description=r["description"],
        date_taken=r["date_taken"], keywords=json.loads(r["keywords"]),
        persons_shown=json.loads(r["persons_shown"]), place=r["place"],
        gps_lat=r["gps_lat"], gps_lon=r["gps_lon"], source=SourceRef(file=r["path"]),
    ) for r in rows]
    return ImageResult(images=images, sources=[i.source for i in images])
