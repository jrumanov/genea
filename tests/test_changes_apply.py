import json
from pathlib import Path

import pytest

from genea.changes.apply import (ABSENT, apply_change, describe_change, mark_manual,
                                 undo_change, xmp_args)
from genea.changes.store import Change, ChangeError, ChangeStore, StaleChange
from genea.exiftool import read_metadata
from genea.index.embed import FakeEmbedder
from genea.tools.curate import propose_change


def _image_change(ctx, path="photos/svatba_1932.jpg", operation="set_fields", payload=None):
    return propose_change(ctx, "image_xmp", path, operation,
                          payload or {"XMP-dc:Description": "Nový popis"}, "test").id


def _ged_change(ctx):
    return propose_change(ctx, "gedcom", "tree.ged", "add_event",
                          {"person_id": "tree.ged#@I9@", "type": "BAPM", "date": "1875"}, "matrika p.2").id


def test_xmp_args():
    change = Change(id="c", created="", target={"kind": "image_xmp", "path": "p.jpg"}, operation="set_fields",
                    payload={"XMP-dc:Title": "T", "XMP-iptcExt:PersonInImage": ["A", "B"], "XMP-photoshop:City": []},
                    rationale="r")
    assert xmp_args(change, Path("p.xmp")) == [
        "-overwrite_original", "-XMP-dc:Title=T", "-XMP-iptcExt:PersonInImage=A",
        "-XMP-iptcExt:PersonInImage=B", "-XMP-photoshop:City^=", "p.xmp"]  # empty: kept as an empty tag
    dated = change.model_copy(update={"payload": {"XMP-photoshop:DateCreated": "1936-07-01", "XMP-dc:Title": ""}})
    assert xmp_args(dated, Path("p.xmp")) == [
        "-overwrite_original", "-XMP-photoshop:DateCreated=1936-07-01", "-XMP-exif:DateTimeOriginal=",
        "-XMP-dc:Title^=", "p.xmp"]
    add = change.model_copy(update={"operation": "add_keywords", "payload": {"keywords": ["x"]}})
    assert xmp_args(add, Path("p.xmp"), ["a", "x"]) == [
        "-overwrite_original", "-XMP-dc:Subject=a", "-XMP-dc:Subject=x", "p.xmp"]  # the whole resulting list
    remove = change.model_copy(update={"operation": "remove_keywords", "payload": {"keywords": ["x"]}})
    assert xmp_args(remove, Path("p.xmp"), []) == ["-overwrite_original", "-XMP-dc:Subject^=", "p.xmp"]
    with pytest.raises(ChangeError):
        xmp_args(add, Path("p.xmp"))


def test_stale_change_is_not_applied(tools):
    change_id = _image_change(tools)
    sidecar = tools.kb.root / "photos" / "svatba_1932.xmp"
    sidecar.write_text(sidecar.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    store = ChangeStore(tools.kb)
    with pytest.raises(StaleChange):
        apply_change(tools.kb, store, change_id, FakeEmbedder())
    assert store.get(change_id).status == "stale"


def test_gedcom_changes_are_export_only(tools):
    change_id = _ged_change(tools)
    store = ChangeStore(tools.kb)
    with pytest.raises(ChangeError, match="export"):
        apply_change(tools.kb, store, change_id, FakeEmbedder())
    assert store.get(change_id).status == "pending"
    done = mark_manual(tools.kb, store, change_id)
    assert (done.status, done.reason) == ("applied", "applied manually")
    with pytest.raises(ChangeError):
        undo_change(tools.kb, store, change_id, FakeEmbedder())


def test_manual_is_only_for_gedcom(tools):
    with pytest.raises(ChangeError, match="GEDCOM"):
        mark_manual(tools.kb, ChangeStore(tools.kb), _image_change(tools))


def test_only_pending_changes_apply(tools):
    change_id = _image_change(tools)
    store = ChangeStore(tools.kb)
    store.reject(change_id, "no")
    with pytest.raises(ChangeError, match="rejected"):
        apply_change(tools.kb, store, change_id, FakeEmbedder())


def test_describe_gedcom_change(tools):
    store = ChangeStore(tools.kb)
    rows = describe_change(tools.kb, store.get(_ged_change(tools)))
    assert ("type", "", "BAPM") in rows


@pytest.mark.exiftool
def test_apply_and_undo_with_existing_sidecar(tools_with_images):
    ctx = tools_with_images
    sidecar = ctx.kb.root / "photos" / "svatba_1932.xmp"
    original = sidecar.read_bytes()
    store = ChangeStore(ctx.kb)
    change_id = _image_change(ctx)
    [(field, before, after)] = describe_change(ctx.kb, store.get(change_id))
    assert (field, after) == ("XMP-dc:Description", "Nový popis")
    assert before.startswith("Svatba Jana")

    applied = apply_change(ctx.kb, store, change_id, FakeEmbedder())
    assert applied.status == "applied"
    assert applied.backup.startswith(".genea/backups/")
    assert read_metadata([sidecar])[0]["XMP-dc:Description"] == "Nový popis"
    row = ctx.conn.execute("SELECT i.description FROM images i JOIN files f ON f.id = i.file_id "
                           "WHERE f.path = 'photos/svatba_1932.jpg'").fetchone()
    assert row["description"] == "Nový popis"

    undone = undo_change(ctx.kb, store, change_id, FakeEmbedder())
    assert (undone.status, undone.reason) == ("rejected", "undone")
    assert sidecar.read_bytes() == original
    log = [json.loads(l)["action"] for l in (ctx.kb.changes_dir / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log == ["propose", "apply", "undo"]


@pytest.mark.exiftool
def test_apply_creates_sidecar_and_undo_removes_it(tools_with_images):
    ctx = tools_with_images
    sidecar = ctx.kb.root / "photos" / "dum.xmp"
    store = ChangeStore(ctx.kb)
    change_id = _image_change(ctx, "photos/dum.jpg", "add_keywords", {"keywords": ["dům"]})
    applied = apply_change(ctx.kb, store, change_id, FakeEmbedder())
    assert applied.backup == ABSENT
    assert read_metadata([sidecar])[0]["XMP-dc:Subject"] in ("dům", ["dům"])
    undo_change(ctx.kb, store, change_id, FakeEmbedder())
    assert not sidecar.exists()


@pytest.mark.exiftool
def test_undo_refuses_when_sidecar_modified_after_apply(tools_with_images):
    ctx = tools_with_images
    sidecar = ctx.kb.root / "photos" / "svatba_1932.xmp"
    store = ChangeStore(ctx.kb)
    change_id = _image_change(ctx)
    apply_change(ctx.kb, store, change_id, FakeEmbedder())
    assert store.get(change_id).post_hash
    sidecar.write_text(sidecar.read_text(encoding="utf-8") + "\n<!-- edited elsewhere -->\n", encoding="utf-8")
    edited = sidecar.read_bytes()
    with pytest.raises(ChangeError, match="modified after"):
        undo_change(ctx.kb, store, change_id, FakeEmbedder())
    assert sidecar.read_bytes() == edited
    assert store.get(change_id).status == "applied"


@pytest.mark.exiftool
def test_undo_refuses_when_sidecar_created_by_apply_was_modified(tools_with_images):
    ctx = tools_with_images
    sidecar = ctx.kb.root / "photos" / "dum.xmp"
    store = ChangeStore(ctx.kb)
    change_id = _image_change(ctx, "photos/dum.jpg", "add_keywords", {"keywords": ["dům"]})
    apply_change(ctx.kb, store, change_id, FakeEmbedder())
    sidecar.write_text(sidecar.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ChangeError, match="modified after"):
        undo_change(ctx.kb, store, change_id, FakeEmbedder())
    assert sidecar.exists()


@pytest.mark.exiftool
def test_undo_refuses_backup_outside_backups_dir(tools_with_images):
    ctx = tools_with_images
    sidecar = ctx.kb.root / "photos" / "svatba_1932.xmp"
    store = ChangeStore(ctx.kb)
    change_id = _image_change(ctx)
    applied = apply_change(ctx.kb, store, change_id, FakeEmbedder())
    store.move(applied, "applied", backup="docs/matrika.pdf")  # as if the change file was hand-edited
    before = sidecar.read_bytes()
    with pytest.raises(ChangeError, match="backup"):
        undo_change(ctx.kb, store, change_id, FakeEmbedder())
    assert sidecar.read_bytes() == before
    assert store.get(change_id).status == "applied"


@pytest.mark.exiftool
def test_undo_without_post_hash_skips_modified_check(tools_with_images):
    ctx = tools_with_images
    sidecar = ctx.kb.root / "photos" / "svatba_1932.xmp"
    original = sidecar.read_bytes()
    store = ChangeStore(ctx.kb)
    change_id = _image_change(ctx)
    applied = apply_change(ctx.kb, store, change_id, FakeEmbedder())
    store.move(applied, "applied", post_hash=None)
    assert undo_change(ctx.kb, store, change_id, FakeEmbedder()).status == "rejected"
    assert sidecar.read_bytes() == original


@pytest.fixture
def embedded_photo(kb_with_images):
    """Make photos/<name> with metadata embedded in the image (no sidecar), index the KB, yield tools."""
    from PIL import Image

    from genea.exiftool import run_exiftool
    from genea.index.indexer import index_kb
    from genea.tools.base import open_tools

    opened = []

    def make(name, *tag_args):
        path = kb_with_images.root / "photos" / name
        Image.new("RGB", (16, 16), "green").save(path)
        run_exiftool(["-overwrite_original", *tag_args, str(path)], kb_with_images.settings.exiftool)
        index_kb(kb_with_images, FakeEmbedder())
        opened.append(open_tools(kb_with_images, FakeEmbedder()))
        return opened[-1]
    yield make
    for ctx in opened:
        ctx.conn.close()


def _image_row(ctx, rel):
    return ctx.conn.execute("SELECT i.* FROM images i JOIN files f ON f.id = i.file_id WHERE f.path = ?",
                            (rel,)).fetchone()


@pytest.mark.exiftool
@pytest.mark.parametrize("remove, left", [(["rodina"], ["svatba"]), (["rodina", "svatba"], [])])
def test_removed_keyword_disappears_despite_embedded_iptc_keywords(embedded_photo, remove, left):
    ctx = embedded_photo("rodina.jpg", "-IPTC:Keywords=rodina", "-IPTC:Keywords=svatba",
                         "-XMP-dc:Subject=rodina", "-XMP-dc:Subject=svatba")
    assert json.loads(_image_row(ctx, "photos/rodina.jpg")["keywords"]) == ["rodina", "svatba"]
    store = ChangeStore(ctx.kb)
    change_id = _image_change(ctx, "photos/rodina.jpg", "remove_keywords", {"keywords": remove})
    [(_, _, after)] = describe_change(ctx.kb, store.get(change_id))
    assert after == ", ".join(left)
    apply_change(ctx.kb, store, change_id, FakeEmbedder())
    assert json.loads(_image_row(ctx, "photos/rodina.jpg")["keywords"]) == left
    add_id = _image_change(ctx, "photos/rodina.jpg", "add_keywords", {"keywords": ["Brno"]})
    apply_change(ctx.kb, store, add_id, FakeEmbedder())
    assert json.loads(_image_row(ctx, "photos/rodina.jpg")["keywords"]) == [*left, "Brno"]
    sidecar = ctx.kb.root / "photos" / "rodina.xmp"
    assert read_metadata([sidecar])[0]["XMP-dc:Subject"] in ([*left, "Brno"], "Brno")  # no empty placeholder


def _keywords(ctx, rel):
    return json.loads(_image_row(ctx, rel)["keywords"])


@pytest.mark.exiftool
@pytest.mark.parametrize("xmp_tags", [(), ("-XMP-dc:Subject=rodina",)], ids=["iptc-only", "iptc-plus-partial-xmp"])
def test_add_keyword_keeps_embedded_iptc_keywords(embedded_photo, xmp_tags):
    ctx = embedded_photo("rodina.jpg", "-IPTC:Keywords=rodina", "-IPTC:Keywords=svatba", *xmp_tags)
    store = ChangeStore(ctx.kb)
    change_id = _image_change(ctx, "photos/rodina.jpg", "add_keywords", {"keywords": ["Brno"]})
    [(_, before, after)] = describe_change(ctx.kb, store.get(change_id))
    assert (before, after) == ("rodina, svatba", "rodina, svatba, Brno")
    apply_change(ctx.kb, store, change_id, FakeEmbedder())
    assert _keywords(ctx, "photos/rodina.jpg") == ["rodina", "svatba", "Brno"]


@pytest.mark.exiftool
@pytest.mark.parametrize("sidecar_first", [False, True], ids=["no-sidecar", "existing-sidecar"])
def test_remove_keyword_from_iptc_only_image(embedded_photo, sidecar_first):
    ctx = embedded_photo("rodina.jpg", "-IPTC:Keywords=rodina", "-IPTC:Keywords=svatba")
    store = ChangeStore(ctx.kb)
    if sidecar_first:  # a sidecar created by an unrelated change must not hide the embedded keywords
        title_id = _image_change(ctx, "photos/rodina.jpg", "set_fields", {"XMP-dc:Title": "Rodina"})
        apply_change(ctx.kb, store, title_id, FakeEmbedder())
        assert _keywords(ctx, "photos/rodina.jpg") == ["rodina", "svatba"]
    change_id = _image_change(ctx, "photos/rodina.jpg", "remove_keywords", {"keywords": ["rodina"]})
    [(_, before, after)] = describe_change(ctx.kb, store.get(change_id))
    assert (before, after) == ("rodina, svatba", "svatba")
    apply_change(ctx.kb, store, change_id, FakeEmbedder())
    assert _keywords(ctx, "photos/rodina.jpg") == ["svatba"]


@pytest.mark.exiftool
def test_add_after_removing_all_keywords_from_iptc_only_image(embedded_photo):
    ctx = embedded_photo("rodina.jpg", "-IPTC:Keywords=rodina", "-IPTC:Keywords=svatba")
    store = ChangeStore(ctx.kb)
    remove_id = _image_change(ctx, "photos/rodina.jpg", "remove_keywords", {"keywords": ["rodina", "svatba"]})
    apply_change(ctx.kb, store, remove_id, FakeEmbedder())
    assert _keywords(ctx, "photos/rodina.jpg") == []
    add_id = _image_change(ctx, "photos/rodina.jpg", "add_keywords", {"keywords": ["Brno"]})
    apply_change(ctx.kb, store, add_id, FakeEmbedder())
    assert _keywords(ctx, "photos/rodina.jpg") == ["Brno"]


@pytest.mark.exiftool
def test_date_correction_beats_embedded_xmp_exif_date(embedded_photo):
    ctx = embedded_photo("datum.jpg", "-XMP-exif:DateTimeOriginal=1935:05:01 12:00:00",
                         "-ExifIFD:DateTimeOriginal=1935:05:01 12:00:00")
    assert _image_row(ctx, "photos/datum.jpg")["date_taken"] == "1935-05-01"
    store = ChangeStore(ctx.kb)
    change_id = _image_change(ctx, "photos/datum.jpg", payload={"XMP-photoshop:DateCreated": "1936-07-01"})
    rows = describe_change(ctx.kb, store.get(change_id))
    assert ("XMP-photoshop:DateCreated", "", "1936-07-01") in rows
    assert ("XMP-exif:DateTimeOriginal", "1935:05:01 12:00:00", "") in rows
    apply_change(ctx.kb, store, change_id, FakeEmbedder())
    assert _image_row(ctx, "photos/datum.jpg")["date_taken"] == "1936-07-01"
    undo_change(ctx.kb, store, change_id, FakeEmbedder())
    assert _image_row(ctx, "photos/datum.jpg")["date_taken"] == "1935-05-01"


@pytest.mark.exiftool
def test_empty_value_clears_embedded_caption(embedded_photo):
    ctx = embedded_photo("popis.jpg", "-IPTC:Caption-Abstract=Old caption", "-XMP-dc:Description=Old caption",
                         "-IFD0:ImageDescription=Old caption")
    assert _image_row(ctx, "photos/popis.jpg")["description"] == "Old caption"
    store = ChangeStore(ctx.kb)
    change_id = _image_change(ctx, "photos/popis.jpg", payload={"XMP-dc:Description": ""})
    apply_change(ctx.kb, store, change_id, FakeEmbedder())
    row = _image_row(ctx, "photos/popis.jpg")
    assert row["description"] is None
    assert "old caption" not in row["search_norm"]
