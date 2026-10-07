import hashlib
import json

import pytest

from genea.changes.store import ChangeError, ChangeStore
from genea.tools.base import ToolError
from genea.tools.curate import propose_change, write_note
from genea.tools.models import SourceRef

WEDDING = "photos/svatba_1932.jpg"


def _propose(tools, payload=None, rationale="GEDCOM marriage F5, Brno 1932"):
    return propose_change(tools, "image_xmp", WEDDING, "set_fields",
                          payload or {"XMP-dc:Description": "Svatba Jana Nováka a Marie Dvořákové"},
                          rationale, [SourceRef(file="tree.ged", line=7)])


def test_write_note(tools):
    result = write_note(tools, "Rodina Novákových", "## Zjištění\nJosef byl sedlák.", ["tree.ged#@I1@"])
    assert result.path.startswith(".genea/notes/")
    assert result.path.endswith("-rodina-novakovych.md")
    text = (tools.kb.root / result.path).read_text(encoding="utf-8")
    assert 'title: "Rodina Novákových"' in text
    assert 'related_persons: ["tree.ged#@I1@"]' in text
    assert text.rstrip().endswith("Josef byl sedlák.")
    again = write_note(tools, "Rodina Novákových", "Druhá poznámka.")
    assert again.path.endswith("-rodina-novakovych-2.md")


def test_write_note_requires_content(tools):
    with pytest.raises(ToolError):
        write_note(tools, "Title", "   ")


def test_propose_image_change(tools):
    result = _propose(tools)
    assert result.created and result.status == "pending" and result.id.startswith("chg-")
    stored = json.loads((tools.kb.changes_dir / "pending" / f"{result.id}.json").read_text(encoding="utf-8"))
    assert stored["target"] == {"kind": "image_xmp", "path": WEDDING}
    sidecar = tools.kb.root / "photos" / "svatba_1932.xmp"
    assert stored["base_hash"] == hashlib.sha256(sidecar.read_bytes()).hexdigest()
    assert stored["sources"] == [{"file": "tree.ged", "line": 7, "page": None, "chunk": None}]


def test_duplicate_is_not_proposed_twice(tools):
    first = _propose(tools)
    second = _propose(tools)
    assert (second.id, second.created) == (first.id, False)
    assert len(ChangeStore(tools.kb).list("pending")) == 1


def test_rejected_change_is_not_reproposed(tools):
    first = _propose(tools)
    ChangeStore(tools.kb).reject(first.id, "wrong people")
    again = _propose(tools)
    assert (again.id, again.created, again.status) == (first.id, False, "rejected")


def test_ids_increment(tools):
    a = _propose(tools, {"XMP-dc:Title": "A"})
    b = _propose(tools, {"XMP-dc:Title": "B"})
    assert int(b.id[-4:]) == int(a.id[-4:]) + 1


@pytest.mark.parametrize("kind, path, operation, payload, message", [
    ("image_xmp", WEDDING, "set_fields", {"EXIF:Make": "x"}, "not allowed"),
    ("image_xmp", WEDDING, "set_fields", {}, "at least one"),
    ("image_xmp", WEDDING, "set_fields", {"XMP-dc:Title": 5}, "string"),
    ("image_xmp", WEDDING, "add_keywords", {"keywords": []}, "keywords"),
    ("image_xmp", WEDDING, "rotate", {}, "Unknown operation"),
    ("image_xmp", "docs/kronika.txt", "set_fields", {"XMP-dc:Title": "x"}, "not an image"),
    ("image_xmp", "../x.jpg", "set_fields", {"XMP-dc:Title": "x"}, "outside"),
    ("image_xmp", "photos/missing.jpg", "set_fields", {"XMP-dc:Title": "x"}, "does not exist"),
    ("gedcom", "tree.ged", "add_event", {"person_id": "tree.ged#@I1@"}, "missing"),
    ("gedcom", "tree.ged", "delete_person", {"person_id": "x"}, "Unknown operation"),
    ("gedcom", "docs/kronika.txt", "add_note", {"person_id": "x", "text": "y"}, "not a GEDCOM"),
    ("video", "tree.ged", "add_note", {}, "target_kind"),
])
def test_invalid_proposals(tools, kind, path, operation, payload, message):
    with pytest.raises(ToolError, match=message):
        propose_change(tools, kind, path, operation, payload, "because")


def test_rationale_required(tools):
    with pytest.raises(ToolError, match="rationale"):
        _propose(tools, rationale=" ")


def test_reject_moves_file_and_logs(tools):
    change = _propose(tools)
    store = ChangeStore(tools.kb)
    rejected = store.reject(change.id, "not needed")
    assert rejected.status == "rejected" and rejected.reason == "not needed"
    assert not (tools.kb.changes_dir / "pending" / f"{change.id}.json").exists()
    assert (tools.kb.changes_dir / "rejected" / f"{change.id}.json").exists()
    log = [json.loads(line) for line in (tools.kb.changes_dir / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [entry["action"] for entry in log] == ["propose", "reject"]
    with pytest.raises(ChangeError):
        store.reject(change.id, "again")
