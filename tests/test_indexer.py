import os
import sqlite3
from pathlib import Path

import pytest

from fixtures.make_kb import damage_pdf
from genea.index.db import SchemaMismatch, open_index
from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb, name_forms, scan_files, source_mtime
from genea.index.progress import IndexBusy, IndexDbLocked, IndexInUse, IndexLock, index_state, is_index_running, read_progress


def _query(kb, sql, params=()):
    conn = open_index(kb, FakeEmbedder())
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_full_index(kb):
    report = index_kb(kb, FakeEmbedder())
    assert (report.ok, report.needs_ocr, report.errors) == (5, 1, 0)
    statuses = {r["path"]: r["status"] for r in _query(kb, "SELECT path, status FROM files")}
    assert statuses["docs/sken.pdf"] == "needs_ocr"
    assert statuses["tree.ged"] == "ok"
    assert _query(kb, "SELECT COUNT(*) FROM persons")[0][0] == 20
    chunks = _query(kb, "SELECT COUNT(*) FROM chunks")[0][0]
    assert chunks >= 5
    assert _query(kb, "SELECT COUNT(*) FROM chunks_fts")[0][0] == chunks
    assert _query(kb, "SELECT COUNT(*) FROM chunks_vec")[0][0] == chunks


def test_second_run_skips_unchanged(kb):
    index_kb(kb, FakeEmbedder())
    report = index_kb(kb, FakeEmbedder())
    assert (report.ok, report.skipped) == (0, 6)


def test_unchanged_files_are_not_read_again(kb, monkeypatch):
    index_kb(kb, FakeEmbedder())

    def fail(*args):
        raise AssertionError("an unchanged file was hashed")
    monkeypatch.setattr("genea.index.indexer.file_hash", fail)
    assert index_kb(kb, FakeEmbedder()).skipped == 6


def test_touched_file_is_hashed_once_and_not_reindexed(kb, monkeypatch):
    index_kb(kb, FakeEmbedder())
    path = kb.root / "docs" / "kronika.txt"
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 100))  # new mtime, same content
    report = index_kb(kb, FakeEmbedder())
    assert (report.ok, report.skipped) == (0, 6)

    def fail(*args):
        raise AssertionError("the new mtime was not remembered")
    monkeypatch.setattr("genea.index.indexer.file_hash", fail)
    assert index_kb(kb, FakeEmbedder()).skipped == 6


def test_source_mtime_covers_the_image_sidecar(tmp_path):
    image, sidecar = tmp_path / "a.jpg", tmp_path / "a.xmp"
    image.write_bytes(b"i")
    os.utime(image, (1000, 1000))
    assert source_mtime(image, "image") == 1000
    sidecar.write_bytes(b"s")
    os.utime(sidecar, (2000, 2000))
    assert source_mtime(image, "image") == 2000
    assert source_mtime(image, "doc") == 1000  # only images have sidecars


def test_changed_and_removed_files(kb):
    index_kb(kb, FakeEmbedder())
    (kb.root / "docs" / "kronika.txt").write_text("Nový text o tom, co dělal Josef Novák.", encoding="utf-8")
    (kb.root / "docs" / "poznamky.md").unlink()
    report = index_kb(kb, FakeEmbedder())
    assert (report.ok, report.removed, report.skipped) == (1, 1, 4)
    paths = {r["path"] for r in _query(kb, "SELECT path FROM files")}
    assert "docs/poznamky.md" not in paths
    assert _query(kb, "SELECT COUNT(*) FROM chunks WHERE text LIKE '%1845%'")[0][0] == 0
    assert _query(kb, "SELECT COUNT(*) FROM chunks_fts")[0][0] == _query(kb, "SELECT COUNT(*) FROM chunks")[0][0]


def test_bad_file_is_recorded_and_others_continue(kb):
    (kb.root / "docs" / "broken.docx").write_bytes(b"not a zip file")
    report = index_kb(kb, FakeEmbedder())
    assert (report.ok, report.errors) == (5, 1)
    [row] = _query(kb, "SELECT status, error_msg FROM files WHERE path = 'docs/broken.docx'")
    assert row["status"] == "error"
    assert row["error_msg"]


def test_links_from_chronicle(kb):
    index_kb(kb, FakeEmbedder())
    rows = _query(kb, """
        SELECT l.person_xref, l.confidence FROM links l
        JOIN chunks c ON l.source_kind = 'chunk' AND c.id = l.source_id
        JOIN files f ON f.id = c.file_id
        WHERE f.path = 'docs/kronika.txt'""")
    found = {(r["person_xref"], r["confidence"]) for r in rows}
    assert {("@I1@", 1.0), ("@I14@", 1.0), ("@I3@", 1.0), ("@I12@", 1.0)} <= found


def test_dimension_change_needs_rebuild(kb):
    index_kb(kb, FakeEmbedder(64))
    with pytest.raises(SchemaMismatch):
        index_kb(kb, FakeEmbedder(32))
    assert index_kb(kb, FakeEmbedder(32), rebuild=True).ok == 5


def test_scan_skips_hidden_and_excluded(kb):
    (kb.root / ".git").mkdir()
    (kb.root / ".git" / "notes.txt").write_text("x", encoding="utf-8")
    rels = [kb.rel(p) for p, _ in scan_files(kb)]
    assert "tree.ged" in rels
    assert not any(r.startswith((".git/", ".genea/", "photos/")) for r in rels)


def test_name_forms():
    forms = dict(name_forms("Jan Josef", "Novák", ["Jan Josef Novák", "Johann Nowak"]))
    assert forms["jan josef novak"] == 1.0
    assert forms["novak jan"] == 1.0
    assert forms["jan novak"] == 1.0
    assert forms["j novak"] == 0.6
    assert forms["johann nowak"] == 1.0


@pytest.mark.exiftool
def test_images_are_indexed_and_linked(kb_with_images):
    report = index_kb(kb_with_images, FakeEmbedder())
    assert report.ok == 8
    assert _query(kb_with_images, "SELECT COUNT(*) FROM images")[0][0] == 3
    linked = {r[0] for r in _query(kb_with_images,
                                   "SELECT person_xref FROM links WHERE source_kind = 'image'")}
    assert "@I16@" in linked


def test_changed_file_leaves_no_orphan_virtual_rows(kb):
    index_kb(kb, FakeEmbedder())
    (kb.root / "docs" / "kronika.txt").write_text("Úplně nový text.", encoding="utf-8")
    (kb.root / "docs" / "poznamky.md").unlink()
    index_kb(kb, FakeEmbedder())
    chunks = _query(kb, "SELECT COUNT(*) FROM chunks")[0][0]
    assert _query(kb, "SELECT COUNT(*) FROM chunks_fts")[0][0] == chunks
    assert _query(kb, "SELECT COUNT(*) FROM chunks_vec")[0][0] == chunks
    assert _query(kb, "SELECT COUNT(*) FROM chunks_vec WHERE chunk_id NOT IN (SELECT id FROM chunks)")[0][0] == 0


def test_embedding_is_batched(kb, monkeypatch):
    from genea.index import indexer

    monkeypatch.setattr(indexer, "EMBED_BATCH", 2)
    sizes = []

    class Spy(FakeEmbedder):
        def embed(self, texts):
            sizes.append(len(texts))
            return super().embed(texts)

    index_kb(kb, Spy())
    assert sizes and max(sizes) <= 2
    assert any(s == 2 for s in sizes)


def test_corrupt_pdf_is_recorded_and_others_continue(kb):
    (kb.root / "docs" / "corrupt.pdf").write_bytes(b"%PDF-1.4 this is not really a pdf")
    report = index_kb(kb, FakeEmbedder())
    assert (report.ok, report.errors) == (5, 1)
    [row] = _query(kb, "SELECT status, error_msg FROM files WHERE path = 'docs/corrupt.pdf'")
    assert row["status"] == "error"
    assert row["error_msg"]


def _unreadable(rel_path):
    """A file_hash that fails for one file, like a locked or online-only (OneDrive) file."""
    from genea.index import indexer

    real = indexer.file_hash

    def fake(path, kind):
        if path.as_posix().endswith(rel_path):
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, kind)
    return fake


def test_unreadable_file_is_recorded_and_the_run_continues(kb, monkeypatch):
    from genea.index import indexer

    monkeypatch.setattr(indexer, "file_hash", _unreadable("docs/poznamky.md"))
    report = index_kb(kb, FakeEmbedder())
    assert (report.ok, report.needs_ocr, report.errors) == (4, 1, 1)
    assert [rel for rel, _ in report.problems if "Permission" in _] == ["docs/poznamky.md"]
    [row] = _query(kb, "SELECT status, error_msg, sha256 FROM files WHERE path = 'docs/poznamky.md'")
    assert row["status"] == "error" and "PermissionError" in row["error_msg"]
    assert _query(kb, "SELECT COUNT(*) FROM links")[0][0] > 0  # links were still rebuilt

    monkeypatch.undo()
    report = index_kb(kb, FakeEmbedder())
    assert (report.ok, report.errors, report.skipped) == (1, 0, 5)
    [row] = _query(kb, "SELECT status FROM files WHERE path = 'docs/poznamky.md'")
    assert row["status"] == "ok"


def test_unreadable_file_on_rerun_replaces_its_old_row(kb, monkeypatch):
    from genea.index import indexer

    index_kb(kb, FakeEmbedder())
    path = kb.root / "docs" / "kronika.txt"
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 100))  # an unchanged mtime would skip the read
    monkeypatch.setattr(indexer, "file_hash", _unreadable("docs/kronika.txt"))
    report = index_kb(kb, FakeEmbedder())
    assert (report.errors, report.skipped) == (1, 5)
    [row] = _query(kb, "SELECT status FROM files WHERE path = 'docs/kronika.txt'")
    assert row["status"] == "error"
    assert _query(kb, "SELECT COUNT(*) FROM chunks_fts")[0][0] == _query(kb, "SELECT COUNT(*) FROM chunks")[0][0]


def test_file_vanishing_mid_run_is_recorded(kb, monkeypatch):
    from pathlib import Path

    from genea.index import indexer

    real_stat, real_scan = Path.stat, indexer.scan_files
    scanned = []

    def stat(self, *args, **kwargs):
        if scanned and self.name == "poznamky.md":
            raise FileNotFoundError(2, "vanished", str(self))
        return real_stat(self, *args, **kwargs)

    def scan(kb):
        found = real_scan(kb)
        scanned.append(True)  # from now on the file is gone
        return found

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(indexer, "scan_files", scan)
    report = index_kb(kb, FakeEmbedder())
    monkeypatch.undo()
    assert (report.ok, report.errors) == (4, 1)
    [row] = _query(kb, "SELECT status, error_msg FROM files WHERE path = 'docs/poznamky.md'")
    assert row["status"] == "error" and "FileNotFoundError" in row["error_msg"]


def _link_outside(link, target):
    """Symlink (or, on Windows without the privilege, a directory junction) pointing outside the KB."""
    import os
    import sys

    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except OSError:
        if sys.platform != "win32" or not target.is_dir():
            pytest.skip("creating symlinks is not permitted here")
        import _winapi

        _winapi.CreateJunction(str(target), str(link))


def test_scan_skips_links_escaping_the_root(kb, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("Mimo znalostní bázi.", encoding="utf-8")
    _link_outside(kb.root / "docs" / "escape", outside)
    rels = [kb.rel(p) for p, _ in scan_files(kb)]
    assert "tree.ged" in rels
    assert not any("secret" in r for r in rels)
    assert index_kb(kb, FakeEmbedder()).errors == 0


def test_scan_skips_file_symlink_escaping_the_root(kb, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("Mimo znalostní bázi.", encoding="utf-8")
    _link_outside(kb.root / "docs" / "secret.txt", outside)
    assert "docs/secret.txt" not in [kb.rel(p) for p, _ in scan_files(kb)]


def _image_rows(kb):
    return [tuple(r) for r in _query(kb, "SELECT f.path, i.title, i.keywords, i.persons_shown, i.place, i.date_taken "
                                         "FROM images i JOIN files f ON f.id = i.file_id ORDER BY f.path")]


@pytest.mark.exiftool
def test_images_are_read_in_one_exiftool_run(kb_with_images, monkeypatch):
    from genea.ingest import images

    calls = []
    real = images.read_metadata

    def counting(paths, exe="exiftool"):
        calls.append(len(paths))
        return real(paths, exe)
    monkeypatch.setattr(images, "read_metadata", counting)
    report = index_kb(kb_with_images, FakeEmbedder())
    assert report.errors == 0
    assert calls == [4]  # three images and one sidecar, in a single run
    assert len(_image_rows(kb_with_images)) == 3


@pytest.mark.exiftool
def test_failed_batch_falls_back_to_one_run_per_image(kb_with_images, monkeypatch):
    from genea.exiftool import ExifToolError
    from genea.ingest import images

    batched = index_kb(kb_with_images, FakeEmbedder(), rebuild=True)
    expected = _image_rows(kb_with_images)
    real = images.read_metadata

    def flaky(paths, exe="exiftool"):
        if len(paths) > 2:
            raise ExifToolError("one bad file spoils the batch")
        return real(paths, exe)
    monkeypatch.setattr(images, "read_metadata", flaky)
    report = index_kb(kb_with_images, FakeEmbedder(), rebuild=True)
    assert (report.ok, report.errors) == (batched.ok, 0)
    assert _image_rows(kb_with_images) == expected


def test_progress_phases_and_final_state(kb):
    seen = []

    def record(progress):
        on_disk = read_progress(kb)  # other processes see the same phase
        seen.append((progress.phase, progress.done, progress.total, on_disk and on_disk.phase))

    index_kb(kb, FakeEmbedder(), on_progress=record)
    phases = [phase for phase, *_ in seen]
    assert phases[0] == "scanning" and phases[-1] == "linking"
    assert phases.index("indexing") < phases.index("linking")
    indexing = [(done, total) for phase, done, total, _ in seen if phase == "indexing"]
    assert indexing[0] == (0, 6) and indexing[-1] == (5, 6)  # reported before each of the 6 files
    assert all(phase == disk for phase, _, _, disk in seen if phase != "scanning")
    assert read_progress(kb) is None
    assert index_state(kb) == "ready"
    assert _query(kb, "SELECT value FROM meta WHERE key = 'index_state'")[0][0] == "complete"


def test_crashed_run_is_interrupted_and_releases_the_lock(kb, monkeypatch):
    def boom(conn):
        raise RuntimeError("crash")

    monkeypatch.setattr("genea.index.indexer.rebuild_links", boom)
    with pytest.raises(RuntimeError):
        index_kb(kb, FakeEmbedder())
    assert index_state(kb) == "interrupted"
    assert read_progress(kb) is None
    monkeypatch.undo()
    index_kb(kb, FakeEmbedder())
    assert index_state(kb) == "ready"


def test_second_run_while_locked_is_refused(kb):
    with IndexLock(kb):
        with pytest.raises(IndexBusy):
            index_kb(kb, FakeEmbedder())


def test_schema_mismatch_releases_the_lock(kb):
    index_kb(kb, FakeEmbedder(64))
    with pytest.raises(SchemaMismatch):
        index_kb(kb, FakeEmbedder(32))
    assert is_index_running(kb) is False


def test_rebuild_of_an_open_index_explains_why(kb, monkeypatch):
    index_kb(kb, FakeEmbedder())
    real_unlink = Path.unlink

    def locked(self, *args, **kwargs):
        if self == kb.index_path:
            raise PermissionError(32, "The process cannot access the file")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    with pytest.raises(IndexInUse, match="genea mcp"):
        index_kb(kb, FakeEmbedder(), rebuild=True)
    assert is_index_running(kb) is False


def _hold_write_lock(kb):
    """Another program in the middle of a write, like a `genea index` started before the index lock existed."""
    other = sqlite3.connect(kb.index_path)
    other.execute("BEGIN EXCLUSIVE")
    return other


def test_database_locked_by_another_writer_is_explained(kb, monkeypatch):
    index_kb(kb, FakeEmbedder())
    monkeypatch.setattr("genea.index.db.BUSY_TIMEOUT", 0.1)
    other = _hold_write_lock(kb)
    try:
        with pytest.raises(IndexDbLocked, match="started before this version"):
            index_kb(kb, FakeEmbedder())
    finally:
        other.close()
    assert is_index_running(kb) is False


def test_damaged_pdf_is_indexed_and_named_in_problems(kb):
    damage_pdf(kb.root / "docs" / "matrika.pdf")
    report = index_kb(kb, FakeEmbedder())
    assert report.errors == 0
    messages = dict(report.problems)
    assert messages["docs/matrika.pdf"].startswith("damaged PDF")
    row = _query(kb, "SELECT status, error_msg FROM files WHERE path = 'docs/matrika.pdf'")[0]
    assert (row["status"], row["error_msg"]) == ("ok", messages["docs/matrika.pdf"])
