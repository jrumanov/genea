"""Change sets: edits proposed by the agent, stored as JSON until the user acts on them.

.genea/changes/<status>/<id>.json, status in pending | applied | rejected | stale,
plus an append-only audit log in .genea/changes/log.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from genea.ingest.images import IMAGE_EXTS, sidecar_for
from genea.kb import KnowledgeBase
from genea.tools.models import SourceRef
from genea.viewer.links import STORED

STATUSES = ("pending", "applied", "rejected", "stale")
STATE_PRECEDENCE = ("rejected", "applied", "stale", "pending")  # latest state first
CHANGE_ID = re.compile(r"chg-\d{8}-\d{4}")
XMP_FIELDS = frozenset({
    "XMP-dc:Title", "XMP-dc:Description", "XMP-dc:Subject", "XMP-iptcExt:PersonInImage",
    "XMP-photoshop:City", "XMP-photoshop:Country", "XMP-photoshop:DateCreated", "XMP-iptcCore:Location",
})
IMAGE_OPERATIONS = ("set_fields", "add_keywords", "remove_keywords")
GEDCOM_REQUIRED = {
    "add_event": ("type",),  # plus person_id or family_id, checked by the analysis
    "update_event": ("person_id", "line"),
    "add_note": ("text",),
    "add_source_citation": ("person_id", "source"),
    "link_child": ("family_id", "child_id"),
    "add_person": ("given", "surname"),
}


class ChangeError(Exception):
    """A change is invalid or cannot be applied in its current state."""


class StaleChange(ChangeError):
    """The target file changed after the change was proposed."""


class Change(BaseModel):
    id: str
    created: str
    target: dict[str, str]
    operation: str
    payload: dict[str, Any]
    rationale: str
    sources: list[SourceRef] = Field(default_factory=list)
    base_hash: str | None = None
    status: str = "pending"
    reason: str | None = None
    backup: str | None = None
    applied_at: str | None = None
    post_hash: str | None = None  # hash of the target right after apply; None for legacy changes


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def target_hash(kb: KnowledgeBase, target: dict[str, str]) -> str:
    """Hash of what a change will modify: the sidecar if it exists (else the image), or the .ged file."""
    path = kb.resolve_inside(target["path"])
    if target["kind"] == "image_xmp":
        path = sidecar_for(path) or path
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_text_list(value) -> bool:
    return isinstance(value, str) or (isinstance(value, list) and all(isinstance(v, str) for v in value))


def validate_change(kb: KnowledgeBase, target_kind: str, target_path: str,
                    operation: str, payload: dict) -> dict[str, str]:
    if target_kind not in ("image_xmp", "gedcom"):
        raise ChangeError("target_kind must be 'image_xmp' or 'gedcom'")
    try:
        path = kb.resolve_inside(target_path)
    except ValueError as exc:
        raise ChangeError(str(exc)) from exc
    if not path.is_file():
        raise ChangeError(f"Target file does not exist: {target_path}")
    rel = kb.rel(path)
    if target_kind == "image_xmp":
        if path.suffix.lower() not in IMAGE_EXTS:
            raise ChangeError(f"{rel} is not an image")
        if operation == "set_fields":
            if not payload:
                raise ChangeError("set_fields needs at least one field")
            bad = sorted(set(payload) - XMP_FIELDS)
            if bad:
                raise ChangeError(f"Fields not allowed: {bad}. Allowed: {sorted(XMP_FIELDS)}")
            for key, value in payload.items():
                if not _is_text_list(value):
                    raise ChangeError(f"{key} must be a string or a list of strings")
            date = payload.get("XMP-photoshop:DateCreated")
            if date is not None and not any(v.strip() for v in ([date] if isinstance(date, str) else date)):
                raise ChangeError("XMP-photoshop:DateCreated cannot be cleared: the image's own embedded date "
                                  "would show through again. Give a date, or leave the field out.")
        elif operation in ("add_keywords", "remove_keywords"):
            keywords = payload.get("keywords")
            if not isinstance(keywords, list) or not keywords or not all(
                    isinstance(k, str) and k.strip() for k in keywords):
                raise ChangeError("payload must be {'keywords': ['...', ...]}")
        else:
            raise ChangeError(f"Unknown operation {operation!r} for image_xmp; use {', '.join(IMAGE_OPERATIONS)}")
    else:
        if path.suffix.lower() != ".ged":
            raise ChangeError(f"{rel} is not a GEDCOM file")
        required = GEDCOM_REQUIRED.get(operation)
        if required is None:
            raise ChangeError(f"Unknown operation {operation!r} for gedcom; use {', '.join(GEDCOM_REQUIRED)}")
        missing = [k for k in required if payload.get(k) in (None, "")]
        if missing:
            raise ChangeError(f"payload for {operation} is missing {missing}")
    return {"kind": target_kind, "path": rel}


class ChangeStore:
    def __init__(self, kb: KnowledgeBase) -> None:
        self.kb = kb
        self.dir = kb.changes_dir
        kb.ensure_dirs()

    def _path(self, status: str, change_id: str):
        return self.dir / status / f"{change_id}.json"

    def _write(self, change: Change) -> None:
        path = self._path(change.status, change.id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(change.model_dump_json(indent=2, context=STORED), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _read(path) -> Change:
        try:
            return Change.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, OSError, UnicodeDecodeError) as exc:
            raise ChangeError(f"Corrupt change file {path}") from exc

    def list(self, status: str | None = None) -> list[Change]:
        """All changes sorted by id. Corrupt files are skipped (get() reports them); an id present in
        several status directories (a crash during move()) is returned once, in its latest state."""
        by_id: dict[str, Change] = {}
        for st in reversed(STATE_PRECEDENCE):  # later (higher precedence) overwrites earlier
            for path in sorted((self.dir / st).glob("*.json")):
                try:
                    by_id[path.stem] = self._read(path)
                except ChangeError:
                    continue
        return sorted((c for c in by_id.values() if status is None or c.status == status), key=lambda c: c.id)

    def get(self, change_id: str) -> Change:
        """Look up a change. If the id exists in several status directories (a crash during move()),
        the non-pending (latest) state wins."""
        if not CHANGE_ID.fullmatch(change_id or ""):
            raise ChangeError(f"No change with id {change_id}")
        for status in STATE_PRECEDENCE:
            path = self._path(status, change_id)
            if path.exists():
                return self._read(path)
        raise ChangeError(f"No change with id {change_id}")

    def _next_id(self) -> str:
        """Next free id of today, from the file names in every status directory, so a corrupt
        change file still reserves its id; names that are not change ids are ignored."""
        prefix = f"chg-{datetime.now():%Y%m%d}-"
        taken = [int(path.stem[len(prefix):]) for st in STATUSES for path in (self.dir / st).glob(f"{prefix}*.json")
                 if CHANGE_ID.fullmatch(path.stem)]
        return f"{prefix}{max(taken, default=0) + 1:04d}"

    def propose(self, target_kind: str, target_path: str, operation: str, payload: dict,
                rationale: str, sources: list[SourceRef]) -> tuple[Change, bool]:
        if not rationale or not rationale.strip():
            raise ChangeError("Explain the change in rationale")
        target = validate_change(self.kb, target_kind, target_path, operation, payload)
        for existing in self.list("pending") + self.list("rejected"):
            if (existing.target, existing.operation, existing.payload) == (target, operation, payload):
                return existing, False
        change = Change(id=self._next_id(), created=now(), target=target, operation=operation,
                        payload=payload, rationale=rationale.strip(), sources=sources,
                        base_hash=target_hash(self.kb, target))
        self._write(change)
        self.log("propose", change)
        return change, True

    def move(self, change: Change, status: str, **updates) -> Change:
        old_path = self._path(change.status, change.id)
        moved = change.model_copy(update={"status": status, **updates})
        self._write(moved)
        if old_path != self._path(status, change.id):
            old_path.unlink(missing_ok=True)
        return moved

    def reject(self, change_id: str, reason: str) -> Change:
        change = self.get(change_id)
        if change.status != "pending":
            raise ChangeError(f"{change.id} is {change.status}; only pending changes can be rejected")
        rejected = self.move(change, "rejected", reason=reason or "rejected")
        self.log("reject", rejected, reason=reason)
        return rejected

    def log(self, action: str, change: Change, **extra) -> None:
        entry = {"ts": now(), "action": action, "id": change.id, "status": change.status, **extra}
        with (self.dir / "log.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
