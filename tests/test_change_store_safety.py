import pytest

from genea.changes.store import ChangeError, ChangeStore
from genea.tools.curate import propose_change


def _propose(ctx, kw="a"):
    return propose_change(ctx, "image_xmp", "photos/dum.jpg", "add_keywords", {"keywords": [kw]}, "r")


def test_corrupt_file_get_raises_change_error_and_list_skips(tools):
    good = _propose(tools, "a")
    store = ChangeStore(tools.kb)
    bad = store.dir / "pending" / "chg-20260101-0001.json"
    bad.write_text('{"id": "chg-2026', encoding="utf-8")
    with pytest.raises(ChangeError, match="Corrupt change file"):
        store.get("chg-20260101-0001")
    assert [c.id for c in store.list()] == [good.id]
    assert _propose(tools, "b").id != good.id  # propose is not crashed by the corrupt file


def test_get_rejects_path_traversal_ids(tools):
    store = ChangeStore(tools.kb)
    outside = tools.kb.changes_dir / "secret.json"
    outside.write_text("{}", encoding="utf-8")
    for bad in ("../secret", "../../changes/secret", "pending/../../secret", "chg-1"):
        with pytest.raises(ChangeError):
            store.get(bad)


def test_duplicate_id_in_two_statuses_prefers_latest_state(tools):
    store = ChangeStore(tools.kb)
    change = store.get(_propose(tools).id)
    applied = change.model_copy(update={"status": "applied"})
    store._write(applied)  # simulates a crash between the write and the unlink in move()
    assert (store.dir / "pending" / f"{change.id}.json").exists()
    assert store.get(change.id).status == "applied"
    listed = store.list()
    assert [c.id for c in listed] == [change.id]
    assert listed[0].status == "applied"
    assert store.list("pending") == [] or store.list("pending")[0].status == "pending"


def test_next_id_counts_corrupt_and_stray_files(tools):
    from datetime import datetime

    store = ChangeStore(tools.kb)
    day = f"{datetime.now():%Y%m%d}"
    (store.dir / "pending" / f"chg-{day}-0005.json").write_text("{corrupt", encoding="utf-8")
    (store.dir / "applied" / f"chg-{day}-0003.json").write_text("{}", encoding="utf-8")
    (store.dir / "rejected" / f"chg-{day}-abc.json").write_text("{}", encoding="utf-8")
    (store.dir / "stale" / f"chg-{day}-9999.txt").write_text("", encoding="utf-8")
    assert _propose(tools).id == f"chg-{day}-0006"
