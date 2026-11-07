"""Export pending GEDCOM changes as a Markdown checklist and a GEDCOM fragment."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from genea.changes.store import Change, ChangeStore
from genea.kb import KnowledgeBase


def _xref(ref: str) -> str:
    return ref.split("#", 1)[-1]  # "tree.ged#@I9@" -> "@I9@"


def describe_operation(op: str, p: dict) -> str:
    """One line describing a GEDCOM operation, for checklists, reports and GENEA notes."""
    if op == "add_event":
        return f"Add {p['type']} to {p.get('person_id') or p.get('family_id')}: date {p.get('date') or '—'}, place {p.get('place') or '—'}"
    if op == "update_event":
        updates = ", ".join(f"{k} → {v}" for k, v in p.items() if k not in ("person_id", "line"))
        return f"Update the event on line {p['line']} of {p['person_id']}: {updates}"
    if op == "add_note":
        return f"Add a note to {p.get('person_id') or p.get('family_id')}: {p['text']}"
    if op == "add_source_citation":
        page = f", page {p['page']}" if p.get("page") else ""
        where = f" ({p['event_type']})" if p.get("event_type") else ""
        return f"Cite source '{p['source']}'{page} on {p['person_id']}{where}"
    if op == "link_child":
        return f"Add {p['child_id']} as a child of family {p['family_id']}"
    details = ", ".join(f"{k} {p[k]}" for k in ("sex", "birth_date", "birth_place") if p.get(k))
    return f"Add a new person {p['given']} {p['surname']}" + (f" ({details})" if details else "")


def _instruction(change: Change) -> str:
    return describe_operation(change.operation, change.payload)


def _text_lines(level: int, tag: str, text: str) -> list[str]:
    first, *rest = (text or "").split("\n")
    return [f"{level} {tag} {first}".rstrip()] + [f"{level + 1} CONT {line}".rstrip() for line in rest]


def _ged_lines(change: Change, n: int) -> list[str]:
    p, op = change.payload, change.operation
    if op == "add_person":
        lines = [f"0 @GENEA{n}@ INDI", f"1 NAME {p['given']} /{p['surname']}/"]
        if p.get("sex"):
            lines.append(f"1 SEX {p['sex']}")
        if p.get("birth_date") or p.get("birth_place"):
            lines.append("1 BIRT")
            if p.get("birth_date"):
                lines.append(f"2 DATE {p['birth_date']}")
            if p.get("birth_place"):
                lines.append(f"2 PLAC {p['birth_place']}")
        return lines
    if op == "link_child":
        return [f"0 {_xref(p['family_id'])} FAM", f"1 CHIL {_xref(p['child_id'])}"]
    lines = ([f"0 {_xref(p['family_id'])} FAM"] if p.get("family_id") else [f"0 {_xref(p['person_id'])} INDI"])
    if op == "add_event":
        lines.append(f"1 {p['type']}")
        if p.get("date"):
            lines.append(f"2 DATE {p['date']}")
        if p.get("place"):
            lines.append(f"2 PLAC {p['place']}")
    elif op == "update_event":
        lines += _text_lines(1, "NOTE", f"genea {change.id}: {_instruction(change)}")
    elif op == "add_note":
        lines += _text_lines(1, "NOTE", p["text"])
    elif op == "add_source_citation":
        level = 1
        if p.get("event_type"):
            lines.append(f"1 {p['event_type']}")
            level = 2
        lines.append(f"{level} SOUR {p['source']}")
        if p.get("page"):
            lines.append(f"{level + 1} PAGE {p['page']}")
    return lines


def export_gedcom_changes(kb: KnowledgeBase, store: ChangeStore) -> tuple[Path, Path] | None:
    changes = [c for c in store.list("pending") if c.target["kind"] == "gedcom"]
    if not changes:
        return None
    day = date.today().isoformat()
    kb.exports_dir.mkdir(parents=True, exist_ok=True)
    md_path = kb.exports_dir / f"{day}-checklist.md"
    ged_path = kb.exports_dir / f"{day}-changes.ged"
    md = ["# GEDCOM changes to make", "",
          f"{len(changes)} pending change(s), exported {datetime.now():%Y-%m-%d %H:%M}.",
          "Make each change in your genealogy program, then run `genea changes apply <id> --manual`.", ""]
    for change in changes:
        cites = ", ".join(s.cite() for s in change.sources) or "none given"
        md += [f"## {change.id} — {change.operation} in {change.target['path']}", "",
               f"- [ ] {_instruction(change)}", f"- Why: {change.rationale}", f"- Sources: {cites}", ""]
    ged = ["0 HEAD", "1 SOUR GENEA", "1 GEDC", "2 VERS 5.5.1", "2 FORM LINEAGE-LINKED", "1 CHAR UTF-8",
           "1 NOTE Changes proposed by genea. Records that reuse existing ids must be merged by hand."]
    for n, change in enumerate(changes, start=1):
        ged += _ged_lines(change, n)
    ged.append("0 TRLR")
    md_path.write_text("\n".join(md), encoding="utf-8")
    ged_path.write_text("\n".join(ged) + "\n", encoding="utf-8")
    return md_path, ged_path
