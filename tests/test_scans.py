from pathlib import Path

from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.index.status import index_summary
from genea.ingest.scans import sibling_md, unread_scans


def test_unread_scans_lists_sample_scan(kb):
    index_kb(kb, FakeEmbedder())
    assert unread_scans(kb) == ["docs/sken.pdf"]


def test_unread_scans_without_index_is_empty(kb):
    assert unread_scans(kb) == []


def test_sibling_md_covers_scan(kb):
    index_kb(kb, FakeEmbedder())
    (kb.root / "docs" / "sken.md").write_text("x", encoding="utf-8")
    assert unread_scans(kb) == []


def test_sibling_md_uppercase_extension():
    assert sibling_md(Path("a/X.PDF")) == Path("a/X.md")


def test_status_hides_covered_scan(kb):
    index_kb(kb, FakeEmbedder())
    before = index_summary(kb)
    assert "docs/sken.pdf" in [p[0] for p in before.problems]
    (kb.root / "docs" / "sken.md").write_text("x", encoding="utf-8")
    after = index_summary(kb)
    assert "docs/sken.pdf" not in [p[0] for p in after.problems]
    assert ("doc", "needs_ocr", 1) in after.counts
