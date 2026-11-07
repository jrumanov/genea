"""Map a parsed GEDCOM line tree to person, family and event records."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from genea.ingest.gedcom.dates import DateRange, parse_gedcom_date
from genea.ingest.gedcom.parser import GedDocument, GedLine

INDIVIDUAL_EVENT_TAGS = frozenset({
    "BIRT", "CHR", "BAPM", "DEAT", "BURI", "CREM", "ADOP", "RESI", "OCCU", "EDUC", "EMIG",
    "IMMI", "NATU", "CENS", "PROB", "WILL", "GRAD", "RETI", "EVEN", "CONF", "FCOM", "ORDN", "RELI",
})
FAMILY_EVENT_TAGS = frozenset({"MARR", "DIV", "ENGA", "MARB", "MARC", "MARL", "ANUL", "EVEN", "CENS", "RESI"})
FAMILY_WRITE_TAGS = frozenset({"MARR", "DIV", "ENGA", "MARB", "MARS"})  # family events the Gramps writer maps
GENEA_NOTE = re.compile(r"^GENEA (CHECK )?v\d+ ")  # notes genea writes into iterations; never evidence


@dataclass
class PersonRec:
    xref: str
    given: str
    surname: str
    sex: str | None
    name_variants: list[str]
    notes: str
    line_no: int


@dataclass
class EventRec:
    owner_xref: str
    type: str
    description: str | None
    date_raw: str | None
    date: DateRange
    place: str | None
    citations: list[dict]
    line_no: int


@dataclass
class FamilyRec:
    xref: str
    husband_xref: str | None
    wife_xref: str | None
    children: list[tuple[str, str]]
    line_no: int


@dataclass
class GedcomData:
    persons: list[PersonRec] = field(default_factory=list)
    families: list[FamilyRec] = field(default_factory=list)
    events: list[EventRec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def split_name(value: str) -> tuple[str, str]:
    """'Jan /Novák/' -> ('Jan', 'Novák')."""
    match = re.match(r"^(.*?)/(.*?)/(.*)$", value or "")
    if not match:
        return (value or "").strip(), ""
    return match.group(1).strip(), match.group(2).strip()


def _citations(line: GedLine, sources: dict[str, str]) -> list[dict]:
    result = []
    for sour in line.all("SOUR"):
        value = sour.value or ""
        if value.startswith("@"):
            entry = {"source": sources.get(value, value), "xref": value}
        else:
            entry = {"source": sour.text()}
        page = sour.first("PAGE")
        if page is not None and page.value:
            entry["page"] = page.text()
        result.append(entry)
    return result


def _event(owner: str, line: GedLine, sources: dict[str, str]) -> EventRec:
    date_line, place_line, type_line = line.first("DATE"), line.first("PLAC"), line.first("TYPE")
    description = line.text() if line.value else (type_line.value if type_line else None)
    raw = date_line.value if date_line else None
    return EventRec(
        owner_xref=owner,
        type=line.tag,
        description=description or None,
        date_raw=raw,
        date=parse_gedcom_date(raw),
        place=place_line.value if place_line else None,
        citations=_citations(line, sources),
        line_no=line.line_no,
    )


def _value(line: GedLine | None) -> str | None:
    return line.value if line is not None and line.value else None


def map_gedcom(doc: GedDocument) -> GedcomData:
    notes = {r.xref: r.text() for r in doc.records if r.tag == "NOTE" and r.xref}
    sources = {
        r.xref: (r.first("TITL").text() if r.first("TITL") else r.xref)
        for r in doc.records if r.tag == "SOUR" and r.xref
    }
    data = GedcomData(warnings=list(doc.warnings))
    pedigree: dict[tuple[str, str], str] = {}

    for record in doc.records:
        if record.tag == "INDI" and record.xref:
            names = record.all("NAME")
            given, surname = split_name(names[0].value or "") if names else ("", "")
            if names:
                given = _value(names[0].first("GIVN")) or given
                surname = _value(names[0].first("SURN")) or surname
            variants: list[str] = []
            for name in names:
                full = " ".join(" ".join(split_name(name.value or "")).split())
                if full and full not in variants:
                    variants.append(full)
            note_texts = [
                notes.get(n.value, "") if (n.value or "").startswith("@") else n.text()
                for n in record.all("NOTE")
            ]
            data.persons.append(PersonRec(
                xref=record.xref, given=given, surname=surname, sex=_value(record.first("SEX")),
                name_variants=variants,
                notes="\n".join(t for t in note_texts if t and not GENEA_NOTE.match(t)),
                line_no=record.line_no,
            ))
            for child in record.children:
                if child.tag in INDIVIDUAL_EVENT_TAGS:
                    data.events.append(_event(record.xref, child, sources))
                elif child.tag == "FAMC" and child.value:
                    pedi = _value(child.first("PEDI"))
                    if pedi:
                        pedigree[(child.value, record.xref)] = pedi.lower()
        elif record.tag == "FAM" and record.xref:
            data.families.append(FamilyRec(
                xref=record.xref,
                husband_xref=_value(record.first("HUSB")),
                wife_xref=_value(record.first("WIFE")),
                children=[(c.value, "birth") for c in record.all("CHIL") if c.value],
                line_no=record.line_no,
            ))
            for child in record.children:
                if child.tag in FAMILY_EVENT_TAGS:
                    data.events.append(_event(record.xref, child, sources))

    for family in data.families:
        family.children = [(c, pedigree.get((family.xref, c), "birth")) for c, _ in family.children]
    return data
