"""Write resolved proposals to Gramps Web: create new objects, then update the one referring to them (spec §6, §8).

Only the object endpoints are used, never POST /api/transactions/, and no marker notes are written.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from genea.analysis.models import WriteRecord, WrittenObject
from genea.analysis.sourcenote import event_description, with_source
from genea.gramps.client import Conflict, GrampsClient, GrampsError
from genea.gramps.resolve import TAG_TO_GRAMPS, Target

CLASSES = {"people": "Person", "families": "Family", "events": "Event", "notes": "Note", "citations": "Citation",
           "places": "Place"}
SINGULAR = {"people": "person", "families": "family", "events": "event", "notes": "note",
            "citations": "citation", "places": "place"}
GENDERS = {"M": 1, "F": 0, "U": 2}
_STATUS = re.compile(r": (\d{3})\b")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class WriteLog:
    """writes.jsonl: append-only, one JSON line per request."""

    def __init__(self, path: Path):
        self.path = path

    def append(self, proposal_id: str, method: str, path: str, status: int, handle: str | None,
               gramps_id: str | None, transaction: int | None, changes: list | None) -> None:
        row = {"ts": _now(), "proposal_id": proposal_id, "method": method, "path": path, "status": status,
               "handle": handle, "gramps_id": gramps_id, "transaction": transaction, "changes": changes}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _status(exc: GrampsError) -> int:
    if isinstance(exc, Conflict):
        return 412
    match = _STATUS.search(str(exc))
    return int(match.group(1)) if match else 0


def _event(type_name: str, date: dict | None, place: str | None, description: str | None = None,
           notes: list[str] | None = None) -> dict:
    out = {"_class": "Event", "type": {"_class": "EventType", "string": type_name}, "place": place or ""}
    if date:
        out["date"] = date
    if description:
        out["description"] = description
    if notes:
        out["note_list"] = notes
    return out


def _add_event_ref(person: dict, event_handle: str, type_name: str, role: str = "Primary") -> dict:
    person = {**person, "event_ref_list": [*(person.get("event_ref_list") or []), {
        "_class": "EventRef", "ref": event_handle, "role": {"_class": "EventRoleType", "string": role}}]}
    index = len(person["event_ref_list"]) - 1
    for name, key in (("Birth", "birth_ref_index"), ("Death", "death_ref_index")):
        if type_name == name and person.get(key, -1) == -1:
            person[key] = index
    return person


class _Writer:
    def __init__(self, client: GrampsClient, log: WriteLog, proposal_id: str):
        self.client, self.log, self.proposal_id = client, log, proposal_id
        self.objects: list[WrittenObject] = []
        self.originals: list[tuple[str, dict]] = []  # (kind, object as read before its first PUT)

    def _transaction(self, kind: str, handle: str) -> int | None:
        try:
            return self.client.latest_transaction(CLASSES[kind], handle)
        except GrampsError:
            return None  # the write itself went through; only its history id is unknown

    def create(self, kind: str, obj: dict) -> str:
        path = f"/api/{kind}/"
        try:
            changes = self.client.create(kind, obj)
        except GrampsError as e:
            self.log.append(self.proposal_id, "POST", path, _status(e), None, None, None, None)
            raise
        handle = next(c["handle"] for c in changes if c.get("_class") == CLASSES[kind])
        # tracked before any further request, so clean-up can delete it whatever fails next
        written = WrittenObject(kind=kind, handle=handle, gramps_id=None, created=True, transaction=0)
        self.objects.append(written)
        try:
            written.gramps_id = self.client.get(kind, handle)[0].get("gramps_id")
        except GrampsError:
            pass  # the orphan message then names the handle
        tid = self._transaction(kind, handle)  # neither lookup raises, so the POST is always logged
        written.transaction = tid or 0
        self.log.append(self.proposal_id, "POST", path, 201, handle, written.gramps_id, tid, changes)
        return handle

    def update(self, kind: str, obj: dict, etag: str) -> None:
        handle = obj["handle"]
        path = f"/api/{kind}/{handle}"
        original = None
        if not any(o.handle == handle for o in self.objects):  # created here: clean-up deletes it instead
            try:
                original = self.client.get(kind, handle)[0]
            except GrampsError as e:
                self.log.append(self.proposal_id, "GET", path, _status(e), handle, obj.get("gramps_id"), None, None)
                raise
        try:
            changes = self.client.update(kind, handle, obj, etag)
        except GrampsError as e:
            self.log.append(self.proposal_id, "PUT", path, _status(e), handle, obj.get("gramps_id"), None, None)
            raise
        if original is not None:
            self.originals.append((kind, original))
        written = WrittenObject(kind=kind, handle=handle, gramps_id=obj.get("gramps_id"), created=False,
                                transaction=0)
        self.objects.append(written)
        tid = self._transaction(kind, handle)
        written.transaction = tid or 0
        self.log.append(self.proposal_id, "PUT", path, 200, handle, written.gramps_id, tid, changes)

    def clean_up(self) -> str:
        """Delete the objects created for this proposal, newest first, then put the updated ones back as they were;
        the suffix for the error message."""
        orphans = []
        created = [o for o in self.objects if o.created]
        for obj in reversed(created):
            path = f"/api/{obj.kind}/{obj.handle}"
            try:
                self.client.delete(obj.kind, obj.handle)
            except GrampsError as e:
                self.log.append(self.proposal_id, "DELETE", path, _status(e), obj.handle, obj.gramps_id, None, None)
                orphans.append(f"{obj.kind} {obj.gramps_id or obj.handle}")
                continue
            self.log.append(self.proposal_id, "DELETE", path, 200, obj.handle, obj.gramps_id,
                            self._transaction(obj.kind, obj.handle), None)
        unrestored = []
        for kind, original in reversed(self.originals):
            handle = original["handle"]
            path = f"/api/{kind}/{handle}"
            try:
                _, etag = self.client.get(kind, handle)
                self.client.update(kind, handle, original, etag)
            except GrampsError as e:
                self.log.append(self.proposal_id, "PUT", path, _status(e), handle, original.get("gramps_id"),
                                None, None)
                unrestored.append(f"{SINGULAR[kind]} {original.get('gramps_id') or handle}")
                continue
            self.log.append(self.proposal_id, "PUT", path, 200, handle, original.get("gramps_id"),
                            self._transaction(kind, handle), None)
        out = ""
        if orphans:
            out = f" (clean-up failed: orphan {', '.join(orphans)}, delete it in Gramps)"
        elif created or (self.originals and not unrestored):
            out = " (cleaned up)"
        if unrestored:
            out += f" ({', '.join(f'{u} could not be restored' for u in unrestored)}; check it in Gramps)"
        return out


def write_proposal(client: GrampsClient, target: Target, log: WriteLog) -> WriteRecord:
    """Raise Conflict (412) or GrampsError after deleting what was created for this proposal, restoring what it
    updated and naming anything that could not be restored."""
    p = target.proposal
    w = _Writer(client, log, p.id)
    try:
        WRITERS[p.operation](w, target)
    except Exception as e:  # not BaseException: Ctrl-C and SystemExit pass through
        suffix = w.clean_up()
        if isinstance(e, GrampsError):
            if not suffix:
                raise
            raise type(e)(f"{e}{suffix}") from e
        raise GrampsError(f"{type(e).__name__}: {e}{suffix}") from e
    return WriteRecord(at=_now(), objects=w.objects)


def _update_event(w: _Writer, t: Target) -> None:
    _, event, etag = t.reads["event"]
    pl = t.proposal.payload
    event = dict(event)
    if t.gramps_date is not None:
        event["date"] = t.gramps_date
    if t.place_handle is not None:
        event["place"] = t.place_handle
    if pl.get("description") is not None:
        event["description"] = pl["description"]
    w.update("events", event, etag)


def _note(w: _Writer, text: str) -> str:
    return w.create("notes", {"_class": "Note", "text": {"_class": "StyledText", "string": text}})


def _owner(t: Target) -> tuple[str, dict, str]:
    return t.reads.get("person") or t.reads["family"]


def _add_event(w: _Writer, t: Target) -> None:
    kind, owner, etag = _owner(t)
    pl = t.proposal.payload
    type_name = TAG_TO_GRAMPS[pl["type"]]
    notes = [_note(w, with_source(pl["note"], t.proposal.sources))] if pl.get("note") else None
    place = t.place_handle
    if t.new_place:
        place = w.create("places", {"_class": "Place", "title": t.new_place,
                                    "name": {"_class": "PlaceName", "value": t.new_place}})
    handle = w.create("events", _event(type_name, t.gramps_date, place, event_description(pl), notes))
    w.update(kind, _add_event_ref(owner, handle, type_name, "Family" if kind == "families" else "Primary"), etag)


def _add_person(w: _Writer, t: Target) -> None:
    pl = t.proposal.payload
    person = {"_class": "Person", "gender": GENDERS.get(pl.get("sex") or "U", 2), "primary_name": {
        "_class": "Name", "first_name": pl["given"],
        "surname_list": [{"_class": "Surname", "surname": pl["surname"]}]}}
    handle = w.create("people", person)
    if not (t.gramps_date or t.place_handle):
        return
    event = w.create("events", _event("Birth", t.gramps_date, t.place_handle))
    stored, etag = w.client.get("people", handle)
    w.update("people", _add_event_ref(stored, event, "Birth"), etag)


def _add_note(w: _Writer, t: Target) -> None:
    kind, owner, etag = _owner(t)
    note = _note(w, with_source(t.proposal.payload["text"], t.proposal.sources))
    w.update(kind, {**owner, "note_list": [*(owner.get("note_list") or []), note]}, etag)


def _add_source_citation(w: _Writer, t: Target) -> None:
    kind, owner, etag = t.reads["owner"]
    citation = w.create("citations", {"_class": "Citation", "source_handle": t.source_handle,
                                      "page": t.proposal.payload.get("page") or ""})
    w.update(kind, {**owner, "citation_list": [*(owner.get("citation_list") or []), citation]}, etag)


def _link_child(w: _Writer, t: Target) -> None:
    _, family, etag = t.reads["family"]
    child_handle = t.child_handle
    w.update("families", {**family, "child_ref_list": [*(family.get("child_ref_list") or []),
                                                       {"_class": "ChildRef", "ref": child_handle}]}, etag)
    # The fake server does not maintain parent_family_list, so the second PUT below runs there; whether the
    # live Gramps Web server does it on the family PUT is checked in Task 9.
    child, child_etag = w.client.get("people", child_handle)
    if family["handle"] not in (child.get("parent_family_list") or []):
        w.update("people", {**child, "parent_family_list": [*(child.get("parent_family_list") or []),
                                                            family["handle"]]}, child_etag)


WRITERS = {"update_event": _update_event, "add_event": _add_event, "add_person": _add_person,
           "add_note": _add_note, "add_source_citation": _add_source_citation, "link_child": _link_child}
