import pytest

from genea.index.db import SchemaMismatch, open_index
from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb, name_forms, scan_files


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
