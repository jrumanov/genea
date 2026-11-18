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


def _blank_pdf(path: Path, pages: int) -> Path:
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path))
    for _ in range(pages):
        c.showPage()
    c.save()
    return path


def test_render_pages_writes_numbered_pngs(tmp_path):
    from PIL import Image
    from genea.ingest.scans import render_pages
    out = tmp_path / "out"
    paths = render_pages(_blank_pdf(tmp_path / "s.pdf", 2), out)
    assert [p.name for p in paths] == ["page-001.png", "page-002.png"]
    with Image.open(paths[0]) as img:
        assert abs(img.width - 595.27 * 200 / 72) <= 2


def test_render_pages_replaces_old_page_only(tmp_path):
    from genea.ingest.scans import render_pages
    out = tmp_path / "out"
    out.mkdir()
    (out / "page-001.png").write_bytes(b"old")
    (out / "notes.txt").write_text("keep", encoding="utf-8")
    render_pages(_blank_pdf(tmp_path / "s.pdf", 1), out)
    assert (out / "page-001.png").read_bytes() != b"old"
    assert (out / "notes.txt").read_text(encoding="utf-8") == "keep"


def test_render_pages_damaged_pdf_raises_scan_error(tmp_path):
    import pytest
    from genea.ingest.scans import ScanError, render_pages
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    with pytest.raises(ScanError):
        render_pages(bad, tmp_path / "out")
