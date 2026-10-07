"""Tolerant, lossless GEDCOM line-tree parser.

Every physical line keeps its original bytes (including the line terminator), so
`GedDocument.serialize()` reproduces the file byte-for-byte. Unknown tags are kept;
malformed lines are kept as `_INVALID` lines and reported as warnings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from genea.ingest.gedcom.ansel import decode_ansel

_LINE_RE = re.compile(r"^\s*(\d{1,2}) +(?:(@[^@\s][^@]*@) +)?([A-Za-z0-9_]+)(?: (.*))?$")
_SPLIT_BYTES_RE = re.compile(rb"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")
_SPLIT_TEXT_RE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")
_CHAR_RE = re.compile(rb"^\s*1 +CHAR +(\S+)", re.MULTILINE)
_BOMS = ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"))


@dataclass
class GedLine:
    level: int
    xref: str | None
    tag: str
    value: str | None
    line_no: int
    raw_bytes: bytes
    children: list[GedLine] = field(default_factory=list)

    def first(self, tag: str) -> GedLine | None:
        return next((c for c in self.children if c.tag == tag), None)

    def all(self, tag: str) -> list[GedLine]:
        return [c for c in self.children if c.tag == tag]

    def text(self) -> str:
        """The value with CONT (new line) and CONC (append) continuation lines applied."""
        parts = [self.value or ""]
        for child in self.children:
            if child.tag == "CONT":
                parts.append("\n" + (child.value or ""))
            elif child.tag == "CONC":
                parts.append(child.value or "")
        return "".join(parts)


@dataclass
class GedDocument:
    path: Path
    encoding: str
    bom: bytes
    lines: list[GedLine]
    records: list[GedLine]
    warnings: list[str]

    def serialize(self) -> bytes:
        return self.bom + b"".join(line.raw_bytes for line in self.lines)


def detect_encoding(data: bytes) -> tuple[str, bytes]:
    """Return (encoding, bom). BOM > UTF-16 null-byte heuristic > `1 CHAR` header > cp1250."""
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return encoding, bom
    head = data[:200]
    if b"\x00" in head:
        return ("utf-16-le" if head[1:2] == b"\x00" else "utf-16-be"), b""
    match = _CHAR_RE.search(data[:4000])
    declared = match.group(1).decode("ascii", "replace").upper() if match else ""
    if declared in ("UTF-8", "UTF8", "ASCII"):
        return "utf-8", b""
    if declared == "ANSEL":
        return "ansel", b""
    return "cp1250", b""


def _split(body: bytes, encoding: str) -> list[bytes]:
    if encoding.startswith("utf-16"):
        text = body.decode(encoding)
        return [piece.encode(encoding) for piece in _SPLIT_TEXT_RE.findall(text)]
    return _SPLIT_BYTES_RE.findall(body)


def _decode(raw: bytes, encoding: str) -> str:
    if encoding == "ansel":
        return decode_ansel(raw)
    return raw.decode(encoding, errors="replace")


def parse_gedcom(path: Path) -> GedDocument:
    data = Path(path).read_bytes()
    encoding, bom = detect_encoding(data)
    body = data[len(bom):]
    warnings: list[str] = []
    if encoding == "utf-8":
        try:
            body.decode("utf-8")
        except UnicodeDecodeError:
            warnings.append("file says UTF-8 but is not valid UTF-8; decoded as CP1250")
            encoding = "cp1250"

    lines: list[GedLine] = []
    records: list[GedLine] = []
    stack: list[GedLine] = []
    for line_no, raw in enumerate(_split(body, encoding), start=1):
        text = _decode(raw, encoding).rstrip("\r\n")
        if not text.strip():
            lines.append(GedLine(-1, None, "_BLANK", None, line_no, raw))
            continue
        match = _LINE_RE.match(text)
        if not match:
            warnings.append(f"line {line_no}: cannot parse {text[:60]!r}")
            lines.append(GedLine(-1, None, "_INVALID", text, line_no, raw))
            continue
        level = int(match.group(1))
        line = GedLine(level, match.group(2), match.group(3).upper(), match.group(4), line_no, raw)
        lines.append(line)
        while stack and stack[-1].level >= level:
            stack.pop()
        if level == 0:
            records.append(line)
        elif not stack:
            warnings.append(f"line {line_no}: level {level} line outside any record; ignored")
            continue
        else:
            if level > stack[-1].level + 1:
                warnings.append(f"line {line_no}: level jumps from {stack[-1].level} to {level}")
            stack[-1].children.append(line)
        stack.append(line)
    return GedDocument(Path(path), encoding, bom, lines, records, warnings)
