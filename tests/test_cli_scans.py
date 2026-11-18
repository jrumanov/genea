from typer.testing import CliRunner

from genea.cli.main import app
from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb

runner = CliRunner()


def _blank_pdf(path, pages=1):
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path))
    for _ in range(pages):
        c.showPage()
    c.save()
    return path


def test_scans_without_index_hints_at_index(kb):
    result = runner.invoke(app, ["--kb", str(kb.root), "scans"])
    assert result.exit_code == 1
    assert "genea index" in result.output


def test_scans_lists_sample_scan(kb):
    index_kb(kb, FakeEmbedder())
    result = runner.invoke(app, ["--kb", str(kb.root), "scans"])
    assert result.exit_code == 0, result.output
    assert "docs/sken.pdf" in result.output
    assert "1 scanned PDF(s) without an md" in result.output


def test_scans_empty_after_md(kb):
    index_kb(kb, FakeEmbedder())
    (kb.root / "docs" / "sken.md").write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["--kb", str(kb.root), "scans"])
    assert result.exit_code == 0, result.output
    assert "No unread scanned PDFs." in result.output


def test_render_pdf_writes_pages(kb, tmp_path):
    out = tmp_path / "pages"
    result = runner.invoke(app, ["--kb", str(kb.root), "render-pdf", str(kb.root / "docs" / "sken.pdf"), str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "page-001.png").exists()
    assert "page-001.png" in result.output


def test_render_pdf_missing_file_exit_2(kb, tmp_path):
    result = runner.invoke(app, ["--kb", str(kb.root), "render-pdf", str(tmp_path / "no.pdf"), str(tmp_path / "o")])
    assert result.exit_code == 2
    assert "Not a PDF file" in result.output


def test_render_pdf_not_a_pdf_exit_2(kb, tmp_path):
    result = runner.invoke(app, ["--kb", str(kb.root), "render-pdf", str(kb.root / "tree.ged"), str(tmp_path / "o")])
    assert result.exit_code == 2
    assert "Not a PDF file" in result.output


def test_render_pdf_damaged_exit_1_no_traceback(kb, tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf at all")
    result = runner.invoke(app, ["--kb", str(kb.root), "render-pdf", str(bad), str(tmp_path / "o")])
    assert result.exit_code == 1
    assert "Traceback" not in result.output


def test_render_pdf_out_dir_is_a_file_exit_1_no_traceback(kb, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["--kb", str(kb.root), "render-pdf", str(kb.root / "docs" / "sken.pdf"), str(blocker)])
    assert result.exit_code == 1
    assert "cannot write" in result.output


def test_render_pdf_bad_dpi_exit_2(kb, tmp_path):
    for dpi in ("0", "-5", "100000"):
        result = runner.invoke(app, ["--kb", str(kb.root), "render-pdf", str(kb.root / "docs" / "sken.pdf"),
                                     str(tmp_path / "o"), "--dpi", dpi])
        assert result.exit_code == 2, dpi
