"""Apply, undo and describe changes. Only XMP sidecars are ever written."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from genea.changes.store import Change, ChangeError, ChangeStore, StaleChange, now, target_hash
from genea.exiftool import ExifToolError, read_metadata, run_exiftool
from genea.index.embed import Embedder
from genea.index.indexer import reindex_file
from genea.ingest.images import default_sidecar, sidecar_for
from genea.kb import KnowledgeBase

ABSENT = "<absent>"  # backup marker: there was no sidecar before the change
KEYWORD_FIELD = "XMP-dc:Subject"
DATE_FIELD = "XMP-photoshop:DateCreated"
# Read before DATE_FIELD (see ingest.images.DATE) and often copied into a new sidecar from the image's
# own XMP; a sidecar whose date genea sets keeps only DATE_FIELD, so the old date cannot shadow it.
SHADOWING_DATE_FIELD = "XMP-exif:DateTimeOriginal"


class ReindexFailed(Exception):
    """The change was applied or undone, but re-indexing the image failed; `genea index` fixes it."""

    def __init__(self, change: Change, error: Exception) -> None:
        super().__init__(f"{type(error).__name__}: {error}")
        self.change = change


def _reindex(kb: KnowledgeBase, embedder: Embedder, image: Path, change: Change) -> None:
    try:
        reindex_file(kb, embedder, image)
    except Exception as exc:  # noqa: BLE001 - the sidecar and the change state are already final
        raise ReindexFailed(change, exc) from exc


def xmp_args(change: Change, sidecar: Path) -> list[str]:
    """ExifTool arguments for the change. An empty value is written as an empty tag (`-TAG^=`), which
    the sidecar keeps, so it clears the field instead of letting the image's embedded value show
    through (dates cannot be empty: they are deleted instead)."""
    args = ["-overwrite_original"]
    if change.operation == "set_fields":
        for key, value in change.payload.items():
            values = value if isinstance(value, list) else [value]
            if not any(values):
                args.append(f"-{key}=" if key == DATE_FIELD else f"-{key}^=")
            else:
                args += [f"-{key}={v}" for v in values]
            if key == DATE_FIELD:
                args.append(f"-{SHADOWING_DATE_FIELD}=")
    elif change.operation == "add_keywords":
        args.append(f"-{KEYWORD_FIELD}-=")  # drop the empty "no keywords" placeholder, if any
        for keyword in change.payload["keywords"]:
            args += [f"-{KEYWORD_FIELD}-={keyword}", f"-{KEYWORD_FIELD}+={keyword}"]  # no duplicates
    elif change.operation == "remove_keywords":
        args += [f"-{KEYWORD_FIELD}-={k}" for k in change.payload["keywords"]]
    else:
        raise ChangeError(f"Unknown image operation {change.operation!r}")
    return [*args, str(sidecar)]


def _require(store: ChangeStore, change_id: str, status: str) -> Change:
    change = store.get(change_id)
    if change.status != status:
        raise ChangeError(f"{change.id} is {change.status}; this needs a {status} change")
    return change


def apply_change(kb: KnowledgeBase, store: ChangeStore, change_id: str, embedder: Embedder) -> Change:
    change = _require(store, change_id, "pending")
    if change.target["kind"] == "gedcom":
        raise ChangeError(
            "GEDCOM changes are not written to the .ged file in this version. Run `genea changes export`, "
            "make the change in your genealogy program, then run `genea changes apply <id> --manual`."
        )
    image = kb.resolve_inside(change.target["path"])
    if target_hash(kb, change.target) != change.base_hash:
        stale = store.move(change, "stale", reason="the file changed after the change was proposed")
        store.log("stale", stale)
        raise StaleChange(f"{change.id}: {change.target['path']} changed after the proposal; "
                          "ask the agent to propose it again.")
    sidecar = default_sidecar(image)
    if sidecar.exists():
        backup = kb.backups_dir / f"{datetime.now():%Y%m%dT%H%M%S%f}" / kb.rel(sidecar)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sidecar, backup)
        backup_ref = kb.rel(backup)
    else:
        backup_ref = ABSENT
    exe = kb.settings.exiftool
    try:
        if backup_ref == ABSENT:
            try:  # start the new sidecar from the XMP already embedded in the image
                run_exiftool(["-tagsfromfile", str(image), "-xmp:all", str(sidecar)], exe)
            except ExifToolError:
                pass  # the image has no XMP; the next call creates the sidecar from scratch
        run_exiftool(xmp_args(change, sidecar), exe)
        if change.operation == "remove_keywords" and KEYWORD_FIELD not in read_metadata([sidecar], exe)[0]:
            # ExifTool drops an emptied list; an empty keyword records "no keywords" in the sidecar,
            # so the image's embedded keywords do not come back.
            run_exiftool(["-overwrite_original", f"-{KEYWORD_FIELD}^=", str(sidecar)], exe)
    except Exception:
        if backup_ref == ABSENT:
            sidecar.unlink(missing_ok=True)
        else:
            shutil.copy2(kb.root / backup_ref, sidecar)
        raise
    post_hash = target_hash(kb, change.target)
    applied = store.move(change, "applied", backup=backup_ref, applied_at=now(), post_hash=post_hash)
    store.log("apply", applied, sidecar=kb.rel(sidecar))
    _reindex(kb, embedder, image, applied)
    return applied


def undo_change(kb: KnowledgeBase, store: ChangeStore, change_id: str, embedder: Embedder) -> Change:
    change = _require(store, change_id, "applied")
    if change.target["kind"] != "image_xmp":
        raise ChangeError("Only image changes written by genea can be undone; "
                          "undo GEDCOM edits in your genealogy program.")
    image = kb.resolve_inside(change.target["path"])
    sidecar = default_sidecar(image)
    backup = None
    if change.backup != ABSENT:
        backup = (kb.root / (change.backup or "")).resolve()
        if not change.backup or not backup.is_relative_to(kb.backups_dir.resolve()) or not backup.is_file():
            raise ChangeError(f"{change.id}: backup {change.backup!r} is not a file inside the backups folder")
    if change.post_hash is not None and target_hash(kb, change.target) != change.post_hash:
        raise ChangeError(f"{change.id}: sidecar was modified after this change was applied; "
                          "refusing to undo to avoid losing edits")
    if backup is None:
        sidecar.unlink(missing_ok=True)
    else:
        shutil.copy2(backup, sidecar)
    undone = store.move(change, "rejected", reason="undone")
    store.log("undo", undone)
    _reindex(kb, embedder, image, undone)
    return undone


def mark_manual(kb: KnowledgeBase, store: ChangeStore, change_id: str) -> Change:
    change = _require(store, change_id, "pending")
    if change.target["kind"] != "gedcom":
        raise ChangeError("--manual is only for GEDCOM changes; image changes are applied by genea")
    done = store.move(change, "applied", reason="applied manually", applied_at=now())
    store.log("apply_manual", done)
    return done


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _current_metadata(kb: KnowledgeBase, change: Change) -> dict | None:
    image = kb.resolve_inside(change.target["path"])
    sidecar = sidecar_for(image)
    try:
        merged: dict = {}
        for record in read_metadata([image] + ([sidecar] if sidecar else []), kb.settings.exiftool):
            merged.update(record)
        return merged
    except ExifToolError:
        return None


def describe_change(kb: KnowledgeBase, change: Change) -> list[tuple[str, str, str]]:
    """(field, before, after) rows; 'before' is '?' when ExifTool is unavailable."""
    if change.target["kind"] != "image_xmp":
        return [(key, "", _fmt(value)) for key, value in change.payload.items()]
    current = _current_metadata(kb, change)
    if change.operation == "set_fields":
        rows = [(key, "?" if current is None else _fmt(current.get(key)), _fmt(value))
                for key, value in change.payload.items()]
        if DATE_FIELD in change.payload and (current is None or SHADOWING_DATE_FIELD in current):
            rows.append((SHADOWING_DATE_FIELD, "?" if current is None else _fmt(current[SHADOWING_DATE_FIELD]), ""))
        return rows
    keywords = change.payload["keywords"]
    adding = change.operation == "add_keywords"
    if current is None:
        return [(KEYWORD_FIELD, "?", ("+ " if adding else "- ") + ", ".join(keywords))]
    raw = current.get(KEYWORD_FIELD)
    before = [str(k) for k in (raw if isinstance(raw, list) else [raw]) if str(k) != ""] if raw is not None else []
    after = list(dict.fromkeys(before + keywords)) if adding else [k for k in before if k not in keywords]
    return [(KEYWORD_FIELD, _fmt(before), _fmt(after))]
