import pytest

from fixtures.make_kb import damage_pdf
from genea.ingest.docs import Chunk, Page, chunk_pages, extract_document


def test_text_markdown_docx(sample_root):
    txt = extract_document(sample_root / "docs" / "kronika.txt")
    assert txt.pages[0].number is None
    assert "Josef Novák" in txt.pages[0].text
    assert "Terezie Dvořáková" in extract_document(sample_root / "docs" / "poznamky.md").pages[0].text
    assert "Karel Novák" in extract_document(sample_root / "docs" / "dopis.docx").pages[0].text


def test_pdf_pages(sample_root):
    pdf = extract_document(sample_root / "docs" / "matrika.pdf")
    assert [p.number for p in pdf.pages] == [1, 2]
    assert "Vaclav Novak" in pdf.pages[1].text
    assert not pdf.needs_ocr


def test_scanned_pdf_needs_ocr(sample_root):
    scan = extract_document(sample_root / "docs" / "sken.pdf")
    assert scan.needs_ocr
    assert scan.pages == []


def test_damaged_pdf_keeps_text_and_collects_warnings(sample_root, capsys):
    path = sample_root / "docs" / "matrika.pdf"
    damage_pdf(path)
    pdf = extract_document(path)
    assert "Vaclav Novak" in pdf.pages[1].text
    assert any("wrong pointing object" in w for w in pdf.warnings)
    assert capsys.readouterr().err == ""  # collected, not printed over the progress bar


def test_intact_pdf_has_no_warnings(sample_root):
    assert extract_document(sample_root / "docs" / "matrika.pdf").warnings == []


def test_cp1250_text_fallback(tmp_path):
    path = tmp_path / "old.txt"
    path.write_bytes("Jan Dvořák".encode("cp1250"))
    assert extract_document(path).pages[0].text == "Jan Dvořák"


def test_unsupported_type(tmp_path):
    path = tmp_path / "x.rtf"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        extract_document(path)


def test_chunking_long_page():
    text = " ".join(f"slovo{i}" for i in range(3000))
    chunks = chunk_pages([Page(7, text)])
    assert len(chunks) > 1
    assert all(len(c.text) <= 3200 for c in chunks)
    assert [c.chunk_no for c in chunks] == list(range(len(chunks)))
    assert all(c.page == 7 for c in chunks)
    last_word = chunks[0].text.split()[-1]
    assert last_word in chunks[1].text  # overlap


def test_chunking_short_pages_continue_numbering():
    assert chunk_pages([Page(1, " short "), Page(2, "second")]) == [
        Chunk(1, 0, "short"), Chunk(2, 1, "second")]


def test_whisper_transcript_has_segments_not_pages(tmp_path):
    path = tmp_path / "2022_05_10-a_b_rozhovor.txt"
    path.write_bytes("﻿0.600000\t8.560000\t Dobre.\r\n8.560000\t10.0\t Čo mám hovoriť?\r\n".encode("utf-8"))
    extract = extract_document(path)
    assert extract.pages == [] and not extract.needs_ocr
    assert [s.text for s in extract.segments] == ["Dobre.", "Čo mám hovoriť?"]


def test_ordinary_text_has_no_segments(sample_root):
    assert extract_document(sample_root / "docs" / "kronika.txt").segments == []
