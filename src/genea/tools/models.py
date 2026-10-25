"""Typed tool results. Every result carries the sources it was built from."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from genea.ingest.transcript import format_time


class SourceRef(BaseModel):
    file: str
    line: int | None = None
    page: int | None = None
    chunk: int | None = None
    start_s: float | None = None

    def cite(self) -> str:
        if self.line is not None:
            return f"[{self.file}:L{self.line}]"
        if self.page is not None:
            return f"[{self.file} p.{self.page}]"
        if self.start_s is not None:
            return f"[{self.file} @{format_time(self.start_s)}]"
        return f"[{self.file}]"


class ToolResult(BaseModel):
    sources: list[SourceRef] = Field(default_factory=list)


class PersonSummary(BaseModel):
    id: str
    name: str
    sex: str | None = None
    birth: str | None = None
    death: str | None = None
    source: SourceRef


class PersonMatches(ToolResult):
    query: str
    candidates: list[PersonSummary]


class EventOut(BaseModel):
    type: str
    description: str | None = None
    date: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    place: str | None = None
    citations: list[dict] = Field(default_factory=list)
    source: SourceRef


class PersonDetail(ToolResult):
    id: str
    name: str
    given: str | None = None
    surname: str | None = None
    sex: str | None = None
    name_variants: list[str] = Field(default_factory=list)
    notes: str | None = None
    events: list[EventOut] = Field(default_factory=list)
    parents: list[PersonSummary] = Field(default_factory=list)
    spouses: list[PersonSummary] = Field(default_factory=list)
    children: list[PersonSummary] = Field(default_factory=list)
    families_as_spouse: list[str] = Field(default_factory=list)
    families_as_child: list[str] = Field(default_factory=list)


class RelativeOut(BaseModel):
    generation: int
    person: PersonSummary


class RelativesResult(ToolResult):
    person: PersonSummary
    relation: str
    relatives: list[RelativeOut]


class TimelineEntry(BaseModel):
    date: str | None = None
    date_from: str | None = None
    role: str
    person_id: str
    person_name: str
    type: str
    place: str | None = None
    source: SourceRef


class Timeline(ToolResult):
    person: PersonSummary
    entries: list[TimelineEntry]


class DocHit(BaseModel):
    file: str
    page: int | None = None
    start_s: float | None = None
    end_s: float | None = None
    kind: str = "text"
    chunk: int
    text: str
    score: float
    source: SourceRef


class SearchResult(ToolResult):
    query: str
    hits: list[DocHit]
    note: str | None = None


class Mention(BaseModel):
    page: int | None = None
    chunk: int
    start_s: float | None = None
    snippet: str


class DocumentMentions(BaseModel):
    file: str
    match: Literal["exact", "variant", "weak"]
    confidence: float
    chunks: int  # matching chunks in this file
    ambiguous: bool
    mentions: list[Mention]  # at most MENTIONS_PER_FILE
    source: SourceRef


class PersonDocuments(ToolResult):
    person: PersonSummary
    total_files: int
    total_chunks: int
    ambiguous_files: int
    page: int
    pages: int
    files: list[DocumentMentions]
    note: str


class DocumentText(ToolResult):
    file: str
    page: int | None = None
    start_s: float | None = None
    next_start_s: float | None = None
    text: str
    truncated: bool


class ImageOut(BaseModel):
    file: str
    sidecar: str | None = None
    title: str | None = None
    description: str | None = None
    date_taken: str | None = None
    keywords: list[str] = Field(default_factory=list)
    persons_shown: list[str] = Field(default_factory=list)
    place: str | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    source: SourceRef


class ImageResult(ToolResult):
    images: list[ImageOut]


class Conflict(BaseModel):
    kind: str
    severity: Literal["error", "needs_review"]
    person_id: str
    message: str
    sources: list[SourceRef] = Field(default_factory=list)


class ConflictResult(ToolResult):
    conflicts: list[Conflict]


class NoteResult(ToolResult):
    path: str


class ChangeResult(ToolResult):
    id: str
    status: str
    created: bool
    message: str
