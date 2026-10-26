"""Every document that mentions a tree person, grouped by file and paged."""

from __future__ import annotations

import json
import math

from genea.index.indexer import EXACT, VARIANT, name_forms
from genea.text import match_span, normalize, snippet
from genea.tools.base import ToolContext, ToolError, parse_person_id
from genea.tools.models import DocumentMentions, Mention, PersonDocuments, SourceRef
from genea.tools.people import summary
from genea.viewer.links import build_link

FILES_PER_PAGE = 6
MENTIONS_PER_FILE = 3
SNIPPET_CHARS = 240

PHOTOS_NOTE = "Photos are not listed here: find them with find_images."
AMBIGUOUS_NOTE = (" Files marked ambiguous also name another person in the tree with the same name, "
                  "so they may be about that person instead.")


def _forms(row) -> dict[str, float]:
    return dict(name_forms(row["given"], row["surname"], json.loads(row["name_variants"]), row["sex"]))


def _match(confidence: float) -> str:
    if confidence >= EXACT:
        return "exact"
    if confidence >= VARIANT:
        return "variant"
    return "weak"


def documents_about(ctx: ToolContext, person_id: str, page: int = 1) -> PersonDocuments:
    """Documents whose text names the person (generated summaries excluded), best evidence first."""
    ged, xref = parse_person_id(ctx, person_id)
    person = summary(ctx.conn, ged, xref)
    me = ctx.conn.execute("SELECT * FROM persons WHERE ged_file = ? AND xref = ?", (ged, xref)).fetchone()
    forms = _forms(me)
    rows = [] if not forms else ctx.conn.execute(  # nothing can be linked without a given name and surname
        "SELECT c.id, c.page, c.chunk_no, c.start_s, c.text, f.path, l.confidence FROM links l "
        "JOIN chunks c ON l.source_kind = 'chunk' AND c.id = l.source_id JOIN files f ON f.id = c.file_id "
        "WHERE l.person_ged_file = ? AND l.person_xref = ? AND c.kind = 'text'", (ged, xref)).fetchall()

    others_forms: dict[tuple[str, str], set[str]] = {}

    def ambiguous(chunk_id: int, text: str) -> bool:
        """Another person linked to this chunk shares a name form with this person, and the text has it."""
        padded = None
        for other in ctx.conn.execute(
                "SELECT p.* FROM links l JOIN persons p ON p.ged_file = l.person_ged_file "
                "AND p.xref = l.person_xref WHERE l.source_kind = 'chunk' AND l.source_id = ? "
                "AND NOT (p.ged_file = ? AND p.xref = ?)", (chunk_id, ged, xref)):
            key = (other["ged_file"], other["xref"])
            if key not in others_forms:
                others_forms[key] = set(_forms(other))
            shared = others_forms[key] & forms.keys()
            if shared:
                padded = padded or f" {normalize(text)} "
                if any(f" {form} " in padded for form in shared):
                    return True
        return False

    by_file: dict[str, list] = {}
    for row in rows:
        by_file.setdefault(row["path"], []).append(row)
    entries = []
    for path, chunks in by_file.items():
        chunks.sort(key=lambda r: (r["page"] or 0, r["start_s"] or 0.0, r["chunk_no"]))
        confidence = max(r["confidence"] for r in chunks)
        entries.append((path, chunks, confidence, any(ambiguous(r["id"], r["text"]) for r in chunks)))
    entries.sort(key=lambda e: (-e[2], -len(e[1]), e[0]))

    pages = max(1, math.ceil(len(entries) / FILES_PER_PAGE))
    if not 1 <= page <= pages:
        raise ToolError(f"page must be between 1 and {pages}")
    files = []
    for path, chunks, confidence, is_ambiguous in entries[(page - 1) * FILES_PER_PAGE:page * FILES_PER_PAGE]:
        mentions = []
        for r in chunks[:MENTIONS_PER_FILE]:
            # centre on the forms that gave this chunk its confidence, so a stronger mention wins
            needles = [form for form, conf in forms.items() if conf >= r["confidence"]]
            found = match_span(r["text"], needles)
            link = build_link(path, page=r["page"], chunk=r["chunk_no"], start_s=r["start_s"],
                              span=(found[0], found[1] - found[0]) if found else None)
            mentions.append(Mention(page=r["page"], chunk=r["chunk_no"], start_s=r["start_s"], link=link,
                                    snippet=snippet(r["text"], needles, SNIPPET_CHARS)))
        first = mentions[0]
        files.append(DocumentMentions(
            file=path, match=_match(confidence), confidence=confidence, chunks=len(chunks),
            ambiguous=is_ambiguous, mentions=mentions,
            source=SourceRef(file=path, page=first.page, chunk=first.chunk, start_s=first.start_s)))
    ambiguous_files = sum(1 for e in entries if e[3])
    return PersonDocuments(
        person=person, total_files=len(entries), total_chunks=sum(len(e[1]) for e in entries),
        ambiguous_files=ambiguous_files, page=page, pages=pages, files=files,
        note=PHOTOS_NOTE + (AMBIGUOUS_NOTE if ambiguous_files else ""),
        sources=[f.source for f in files])
