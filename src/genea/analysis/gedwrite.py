"""Write-side GEDCOM helpers: insert or replace lines of a losslessly parsed file, keeping every other byte."""

from __future__ import annotations

from collections import defaultdict

from genea.ingest.gedcom.parser import GedDocument

CONC_LIMIT = 248  # characters of a value per line; with level and tag the line stays under GEDCOM's 255
WRITABLE = {"utf-8": "utf-8", "cp1250": "cp1250"}  # parser encoding -> codec used for new lines


class GedWriteError(Exception):
    """The file cannot be written, or an edit cannot be made."""


def split_value(text: str, limit: int = CONC_LIMIT) -> list[str]:
    """Pieces for CONC lines. Never split at a space: importers may trim trailing spaces (GEDCOM 5.5.1)."""
    pieces = []
    while len(text) > limit:
        cut = limit
        while cut > 1 and (text[cut - 1] == " " or text[cut] == " "):
            cut -= 1
        if cut <= 1:  # nothing but spaces around the limit: a split there is unavoidable
            cut = limit
        pieces.append(text[:cut])
        text = text[cut:]
    pieces.append(text)
    return pieces


def text_lines(level: int, tag: str, text: str) -> list[str]:
    out = []
    for n, paragraph in enumerate((text or "").split("\n")):
        first, *rest = split_value(paragraph)
        out.append(f"{level} {tag} {first}".rstrip() if n == 0 else f"{level + 1} CONT {first}".rstrip())
        out += [f"{level + 1} CONC {piece}" for piece in rest]
    return out


class GedEditor:
    """Collects insertions and replacements against the original lines, then writes the new file.

    Indexes are positions in doc.lines (line_no - 1). `owner` names the proposal being applied, so each
    proposal's changes can be shown as a diff hunk (`log`)."""

    def __init__(self, doc: GedDocument) -> None:
        if doc.encoding not in WRITABLE:
            raise GedWriteError(f"genea can only write UTF-8 or ANSI (CP1250) GEDCOM files; {doc.path.name} is "
                                f"{doc.encoding.upper()}. Export the tree from Gramps as UTF-8.")
        self.doc = doc
        self.codec = WRITABLE[doc.encoding]
        first = doc.lines[0].raw_bytes if doc.lines else b"\n"
        self.newline = "\r\n" if first.endswith(b"\r\n") else "\r" if first.endswith(b"\r") else "\n"
        self._records = {line.xref: i for i, line in enumerate(doc.lines) if line.level == 0 and line.xref}
        self._after: dict[int, list[str]] = defaultdict(list)
        self._replace: dict[int, str] = {}
        self._new: list[str] = []
        self.owner: str | None = None
        self.log: dict[str, list[str]] = {}

    def xrefs(self) -> set[str]:
        return set(self._records)

    def record_index(self, xref: str) -> int:
        return self._records[xref]

    def subtree_end(self, index: int) -> int:
        """Index of the last line belonging to the line at `index` (itself if it has no children)."""
        level, end = self.doc.lines[index].level, index
        for j in range(index + 1, len(self.doc.lines)):
            line = self.doc.lines[j]
            if line.level < 0:  # blank or unparsable line: neither ends nor extends the subtree
                continue
            if line.level <= level:
                break
            end = j
        return end

    def insert_after(self, index: int, lines: list[str]) -> None:
        self._check(lines)
        self._after[index].extend(lines)
        self._log([f" {self._text(index)}"] + [f"+{line}" for line in lines])

    def replace(self, index: int, text: str) -> None:
        if index in self._replace:
            raise GedWriteError(f"line {index + 1} would be changed twice")
        self._check([text])
        self._replace[index] = text
        self._log([f"-{self._text(index)}", f"+{text}"])

    def add_record(self, lines: list[str]) -> None:
        self._check(lines)
        self._new.extend(lines)
        self._log([f"+{line}" for line in lines])

    def snapshot(self):
        return ({k: list(v) for k, v in self._after.items()}, dict(self._replace), list(self._new),
                {k: list(v) for k, v in self.log.items()})

    def restore(self, snap) -> None:
        after, replace, new, log = snap
        self._after = defaultdict(list, {k: list(v) for k, v in after.items()})
        self._replace, self._new = dict(replace), list(new)
        self.log = {k: list(v) for k, v in log.items()}

    def serialize(self) -> bytes:
        out = bytearray(self.doc.bom)
        start = len(out)

        def emit(lines: list[str]) -> None:
            if not lines:
                return
            if len(out) > start and not out.endswith((b"\n", b"\r")):  # the original last line had no newline
                out.extend(self.newline.encode(self.codec))
            for line in lines:
                out.extend(self._encode(line))

        trailer = next((i for i in range(len(self.doc.lines) - 1, -1, -1)
                        if self.doc.lines[i].level == 0 and self.doc.lines[i].tag == "TRLR"), None)
        for i, line in enumerate(self.doc.lines):
            if i == trailer:
                emit(self._new)
            if i in self._replace:
                out.extend(self._encode(self._replace[i]))
            else:
                out.extend(line.raw_bytes)
            emit(self._after.get(i, []))
        if trailer is None:
            emit(self._new)
        return bytes(out)

    def _text(self, index: int) -> str:
        return self.doc.lines[index].raw_bytes.decode(self.codec, errors="replace").rstrip("\r\n")

    def _encode(self, text: str) -> bytes:
        try:
            return (text + self.newline).encode(self.codec)
        except UnicodeEncodeError:
            raise GedWriteError(f"{text[:60]!r} cannot be written in {self.doc.encoding.upper()}") from None

    def _check(self, lines: list[str]) -> None:
        for line in lines:
            self._encode(line)

    def _log(self, lines: list[str]) -> None:
        if self.owner:
            self.log.setdefault(self.owner, []).extend(lines)
