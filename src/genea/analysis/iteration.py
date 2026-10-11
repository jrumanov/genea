"""Build an iteration: a copy of the real tree with every active proposal applied and marked by a GENEA note.

Always built from the real .ged, never from a previous iteration: proposals point at lines of the real tree.
"""

from __future__ import annotations

import difflib
import hashlib
from datetime import date

from genea.analysis import store
from genea.analysis.gedwrite import GedEditor, GedWriteError, text_lines
from genea.analysis.models import Analysis, BuildResult, Finding, Proposal
from genea.analysis.report import render_report
from genea.changes.export import describe_operation
from genea.ingest.gedcom.parser import GedLine, parse_gedcom
from genea.kb import KnowledgeBase
from genea.tools.base import ToolError


class Stale(Exception):
    """The proposal no longer fits the tree."""


def _xref(ref: str) -> str:
    return ref.split("#", 1)[-1]


def _value(line: GedLine, tag: str) -> str | None:
    child = line.first(tag)
    return child.value if child is not None and child.value else None


def _plain(text: str) -> str:
    return text.replace("→", "->")  # describe_operation uses arrows; CP1250 has none


class _Builder:
    def __init__(self, editor: GedEditor, ged_file: str, version: int, day: str) -> None:
        self.editor, self.ged_file, self.version, self.day = editor, ged_file, version, day
        self.lines = editor.doc.lines
        self.new_ids: dict[str, str] = {}  # add_person proposal id -> @GENEA<n>@
        self.famc: dict[str, list[str]] = {}  # new person xref -> family xrefs
        self.updated: dict[int, str] = {}  # event line index -> id of the proposal that changed it

    def record(self, ref: str) -> int:
        try:
            return self.editor.record_index(_xref(ref))
        except KeyError:
            raise Stale(f"{ref} is no longer in the tree") from None

    def marker(self, p: Proposal, base: str | None = None) -> str:
        finding = f", finding {p.finding_id}" if p.finding_id else ""
        cites = ", ".join(s.cite() for s in p.sources)
        text = (f"GENEA v{self.version} {self.day} {p.id} ({p.author}{finding}): "
                f"{describe_operation(p.operation, p.payload)}. Why: {p.rationale} Evidence: {cites}.")
        if base:
            text += f" Base value: {base}."
        if base or any(s.line is not None for s in p.sources):
            text += " Line numbers refer to the base file."
        return _plain(text)

    def add_event(self, p: Proposal) -> None:
        rec = self.record(p.payload["person_id"])
        lines = [f"1 {p.payload['type']}"]
        if p.payload.get("date"):
            lines.append(f"2 DATE {p.payload['date']}")
        if p.payload.get("place"):
            lines.append(f"2 PLAC {p.payload['place']}")
        self.editor.insert_after(self.editor.subtree_end(rec), lines + text_lines(2, "NOTE", self.marker(p)))

    def _find_event(self, rec: int, line_no: int, anchor: dict) -> int:
        end = self.editor.subtree_end(rec)

        def fits(i: int) -> bool:
            line = self.lines[i]
            return (line.level == 1 and line.tag == anchor.get("type") and _value(line, "DATE") == anchor.get("date")
                    and _value(line, "PLAC") == anchor.get("place"))

        if rec < line_no - 1 <= end and fits(line_no - 1):
            return line_no - 1
        matches = [i for i in range(rec + 1, end + 1) if fits(i)]
        if len(matches) == 1:
            return matches[0]
        what = f"the {anchor.get('type')} event ({anchor.get('date') or 'no date'})"
        raise Stale(f"{what} is no longer in the record" if not matches else f"{what} is in the record more than once")

    def update_event(self, p: Proposal) -> None:
        rec = self.record(p.payload["person_id"])
        anchor = p.anchor or {}
        event = self._find_event(rec, int(p.payload["line"]), anchor)
        if event in self.updated:  # two proposals on one event: the first one recorded wins
            raise Stale(f"the {anchor.get('type')} event is already changed by {self.updated[event]} "
                        "in this iteration; withdraw one of them")
        line = self.lines[event]
        for field, tag in (("date", "DATE"), ("place", "PLAC")):
            value = p.payload.get(field)
            if not value:
                continue
            child = line.first(tag)
            text = f"{line.level + 1} {tag} {value}"
            if child is None:
                self.editor.insert_after(event, [text])
            else:
                self.editor.replace(child.line_no - 1, text)
        base = (f"{anchor.get('date') or '-'}, {anchor.get('place') or '-'} "
                f"[{self.ged_file}:L{p.payload['line']}, base file]")
        self.editor.insert_after(self.editor.subtree_end(event), text_lines(line.level + 1, "NOTE", self.marker(p, base)))
        self.updated[event] = p.id

    def add_note(self, p: Proposal) -> None:
        rec = self.record(p.payload["person_id"])
        lines = text_lines(1, "NOTE", p.payload["text"]) + text_lines(1, "NOTE", self.marker(p))
        self.editor.insert_after(self.editor.subtree_end(rec), lines)

    def add_source_citation(self, p: Proposal) -> None:
        rec = self.record(p.payload["person_id"])
        owner, level = rec, 1
        event_type = p.payload.get("event_type")
        if event_type:
            end = self.editor.subtree_end(rec)
            events = [i for i in range(rec + 1, end + 1) if self.lines[i].level == 1 and self.lines[i].tag == event_type]
            if len(events) != 1:
                raise Stale(f"the person has {len(events)} {event_type} events, not exactly one")
            owner, level = events[0], 2
        lines = text_lines(level, "SOUR", p.payload["source"])
        if p.payload.get("page"):
            lines.append(f"{level + 1} PAGE {p.payload['page']}")
        self.editor.insert_after(self.editor.subtree_end(owner), lines + text_lines(level, "NOTE", self.marker(p)))

    def link_child(self, p: Proposal) -> None:
        fam = self.record(p.payload["family_id"])
        fam_xref = _xref(p.payload["family_id"])
        child = p.payload["child_id"]
        new = child.startswith("proposal:")
        if new:
            child_xref = self.new_ids.get(child.split(":", 1)[1])
            if child_xref is None:
                raise Stale(f"{child} was not added to this iteration")
        else:
            child_xref, child_rec = _xref(child), self.record(child)
        if any(c.tag == "CHIL" and c.value == child_xref for c in self.lines[fam].children):
            raise Stale(f"{child} is already a child of this family")
        self.editor.insert_after(self.editor.subtree_end(fam), [f"1 CHIL {child_xref}"] + text_lines(1, "NOTE", self.marker(p)))
        if not new:  # a new person gets its FAMC line in add_person
            self.editor.insert_after(self.editor.subtree_end(child_rec), [f"1 FAMC {fam_xref}"])

    def add_person(self, p: Proposal) -> None:
        xref, pl = self.new_ids[p.id], p.payload
        lines = [f"0 {xref} INDI", f"1 NAME {pl['given']} /{pl['surname']}/"]
        if pl.get("sex"):
            lines.append(f"1 SEX {pl['sex']}")
        if pl.get("birth_date") or pl.get("birth_place"):
            lines.append("1 BIRT")
            if pl.get("birth_date"):
                lines.append(f"2 DATE {pl['birth_date']}")
            if pl.get("birth_place"):
                lines.append(f"2 PLAC {pl['birth_place']}")
        lines += [f"1 FAMC {fam}" for fam in self.famc.get(xref, [])]
        self.editor.add_record(lines + text_lines(1, "NOTE", self.marker(p)))

    def check(self, f: Finding) -> bool:
        target = f.person_ids[0] if f.person_ids else f.family_id
        if not target:
            return False
        try:
            rec = self.record(target)
        except Stale:
            return False
        cites = ", ".join(s.cite() for s in f.sources)
        text = (f"GENEA CHECK v{self.version} {self.day} {f.id} ({f.severity}): {f.message} Evidence: {cites}. "
                "Line numbers refer to the base file.")
        try:
            self.editor.insert_after(self.editor.subtree_end(rec), text_lines(1, "NOTE", _plain(text)))
        except GedWriteError:
            return False
        return True


def _diff(base: bytes, new: bytes, editor: GedEditor, ged_file: str) -> str:
    skip = len(editor.doc.bom)
    old_lines = base[skip:].decode(editor.codec, errors="replace").splitlines()
    new_lines = new[skip:].decode(editor.codec, errors="replace").splitlines()
    return "\n".join(difflib.unified_diff(old_lines, new_lines, f"a/{ged_file}", f"b/{ged_file}", lineterm="")) + "\n"


def build(kb: KnowledgeBase, analysis: Analysis) -> BuildResult:
    path = kb.resolve_inside(analysis.ged_file)
    base = path.read_bytes()
    try:
        editor = GedEditor(parse_gedcom(path))
    except GedWriteError as exc:
        raise ToolError(str(exc)) from exc
    version = analysis.iterations + 1
    b = _Builder(editor, analysis.ged_file, version, date.today().isoformat())
    candidates = [p for p in analysis.proposals if p.status in ("active", "stale")]

    taken, n = editor.xrefs(), 0
    for p in candidates:
        if p.operation == "add_person":
            n += 1
            while f"@GENEA{n}@" in taken:
                n += 1
            b.new_ids[p.id] = f"@GENEA{n}@"
    for p in candidates:  # FAMC lines of new people, written with their record
        child = p.payload.get("child_id", "")
        if p.operation == "link_child" and child.startswith("proposal:"):
            new_xref, fam = b.new_ids.get(child.split(":", 1)[1]), _xref(p.payload["family_id"])
            if new_xref and fam in taken:
                b.famc.setdefault(new_xref, []).append(fam)

    applied, stale = [], []
    for p in sorted(candidates, key=lambda p: p.operation != "add_person"):  # new people first
        snap = editor.snapshot()
        editor.owner = p.id
        try:
            getattr(b, p.operation)(p)
        except (Stale, GedWriteError) as exc:
            editor.restore(snap)
            p.status, p.stale_reason = "stale", str(exc)
            stale.append(p.id)
            b.new_ids.pop(p.id, None)
        else:
            p.status, p.stale_reason = "active", None
            applied.append(p.id)
    editor.owner = None
    answered = {p.finding_id for p in analysis.proposals if p.status == "active"}
    checks = sum(b.check(f) for f in analysis.findings
                 if f.severity != "info" and f.id not in analysis.dismissed and f.id not in answered)

    data = editor.serialize()
    folder = store.analysis_dir(kb, analysis.name)
    ged_out, diff_out, md_out = (folder / f"v{version}.{ext}" for ext in ("ged", "diff", "md"))
    store.write_atomic(ged_out, data)
    store.write_atomic(diff_out, _diff(base, data, editor, analysis.ged_file))
    analysis.iterations = version
    store.write_atomic(md_out, render_report(analysis, version, editor.log))
    message = (f"Wrote v{version}: {len(applied)} proposal(s) applied, {len(stale)} stale, {checks} CHECK note(s). "
               "The real tree is unchanged.")
    if hashlib.sha256(base).hexdigest() != analysis.base_hash:
        message += " The tree changed since the analysis; run analyze again to refresh the findings."
    return BuildResult(name=analysis.name, version=version, iteration=kb.rel(ged_out), diff=kb.rel(diff_out),
                       report=kb.rel(md_out), applied=applied, stale=stale, checks=checks, message=message)
