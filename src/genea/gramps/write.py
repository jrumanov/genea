"""Write resolved proposals to Gramps Web: create new objects, then update the one referring to them (spec §6, §8).

Only the object endpoints are used, never POST /api/transactions/, and no marker notes are written.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from genea.analysis.models import WriteRecord, WrittenObject
from genea.gramps.client import Conflict, GrampsClient, GrampsError
from genea.gramps.resolve import TAG_TO_GRAMPS, Target

CLASSES = {"people": "Person", "families": "Family", "events": "Event", "notes": "Note", "citations": "Citation"}
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


def _event(type_name: str, date: dict | None, place: str | None) -> dict:
    out = {"_class": "Event", "type": {"_class": "EventType", "string": type_name}, "place": place or ""}
    if date:
        out["date"] = date
    return out


def _add_event_ref(person: dict, event_handle: str, type_name: str) -> dict:
    person = {**person, "event_ref_list": [*(person.get("event_ref_list") or []), {
        "_class": "EventRef", "ref": event_handle, "role": {"_class": "EventRoleType", "string": "Primary"}}]}
    index = len(person["event_ref_list"]) - 1
    for name, key in (("Birth", "birth_ref_index"), ("Death", "death_ref_index")):
        if type_name == name and person.get(key, -1) == -1:
            person[key] = index
    return person


class _Writer:
    def __init__(self, client: GrampsClient, log: WriteLog, proposal_id: str):
        self.client, self.log, self.proposal_id = client, log, proposal_id
        self.objects: list[WrittenObject] = []

    def _done(self, method: str, path: str, status: int, kind: str, handle: str, gramps_id: str | None,
              changes: list, created: bool) -> None:
        tid = self.client.latest_transaction(CLASSES[kind], handle)
        self.log.append(self.proposal_id, method, path, status, handle, gramps_id, tid, changes)
        self.objects.append(WrittenObject(kind=kind, handle=handle, gramps_id=gramps_id, created=created,
                                          transaction=tid))

    def create(self, kind: str, obj: dict) -> str:
        path = f"/api/{kind}/"
        try:
            changes = self.client.create(kind, obj)
        except GrampsError as e:
            self.log.append(self.proposal_id, "POST", path, _status(e), None, None, None, None)
            raise
        handle = next(c["handle"] for c in changes if c.get("_class") == CLASSES[kind])
        stored, _ = self.client.get(kind, handle)
        self._done("POST", path, 201, kind, handle, stored.get("gramps_id"), changes, True)
        return handle

    def update(self, kind: str, obj: dict, etag: str) -> None:
        handle = obj["handle"]
        path = f"/api/{kind}/{handle}"
        try:
            changes = self.client.update(kind, handle, obj, etag)
        except GrampsError as e:
            self.log.append(self.proposal_id, "PUT", path, _status(e), handle, obj.get("gramps_id"), None, None)
            raise
        self._done("PUT", path, 200, kind, handle, obj.get("gramps_id"), changes, False)

    def clean_up(self) -> str:
        """Delete the objects created for this proposal, newest first; the suffix for the error message."""
        orphans = []
        for obj in reversed([o for o in self.objects if o.created]):
            path = f"/api/{obj.kind}/{obj.handle}"
            try:
                self.client.delete(obj.kind, obj.handle)
            except GrampsError as e:
                self.log.append(self.proposal_id, "DELETE", path, _status(e), obj.handle, obj.gramps_id, None, None)
                orphans.append(f"{obj.kind} {obj.gramps_id or obj.handle}")
                continue
            tid = self.client.latest_transaction(CLASSES[obj.kind], obj.handle)
            self.log.append(self.proposal_id, "DELETE", path, 200, obj.handle, obj.gramps_id, tid, None)
        if orphans:
            return f" (clean-up failed: orphan {', '.join(orphans)}, delete it in Gramps)"
        return " (cleaned up)"


def write_proposal(client: GrampsClient, target: Target, log: WriteLog) -> WriteRecord:
    """Raise Conflict (412) or GrampsError after deleting what was created for this proposal."""
    p = target.proposal
    w = _Writer(client, log, p.id)
    try:
        globals()[f"_{p.operation}"](w, target)
    except GrampsError as e:
        if not any(o.created for o in w.objects):
            raise
        raise type(e)(f"{e}{w.clean_up()}") from e
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


def _add_event(w: _Writer, t: Target) -> None:
    _, person, etag = t.reads["person"]
    type_name = TAG_TO_GRAMPS[t.proposal.payload["type"]]
    handle = w.create("events", _event(type_name, t.gramps_date, t.place_handle))
    w.update("people", _add_event_ref(person, handle, type_name), etag)


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
    _, person, etag = t.reads["person"]
    note = w.create("notes", {"_class": "Note", "text": {"_class": "StyledText",
                                                         "string": t.proposal.payload["text"]}})
    w.update("people", {**person, "note_list": [*(person.get("note_list") or []), note]}, etag)


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
