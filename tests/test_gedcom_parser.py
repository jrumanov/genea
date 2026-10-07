from pathlib import Path

import pytest

from genea.ingest.gedcom.parser import detect_encoding, parse_gedcom

FIXTURE = Path(__file__).parent / "fixtures" / "kb_sample" / "tree.ged"
PRIVATE = sorted((Path(__file__).parent / "private").glob("*.ged"))


def _record(doc, xref):
    return next(r for r in doc.records if r.xref == xref)


def test_parses_fixture():
    doc = parse_gedcom(FIXTURE)
    assert doc.encoding == "utf-8"
    assert doc.warnings == []
    assert len([r for r in doc.records if r.tag == "INDI"]) == 20
    assert len([r for r in doc.records if r.tag == "FAM"]) == 6


def test_tree_structure_and_line_numbers():
    doc = parse_gedcom(FIXTURE)
    i1 = _record(doc, "@I1@")
    assert i1.line_no == 7
    birth = i1.first("BIRT")
    assert birth.first("DATE").value == "12 MAR 1820"
    assert birth.line_no == 13
    assert [n.value for n in i1.all("NAME")] == ["Josef /Novák/", "Joseph /Nowak/"]


def test_cont_and_conc():
    note = _record(parse_gedcom(FIXTURE), "@N1@")
    assert note.text() == "Sedlák v Brně, uváděn v kronice obce.\nDruhý řádek poznámky."


def test_round_trip_fixture():
    assert parse_gedcom(FIXTURE).serialize() == FIXTURE.read_bytes()


def test_round_trip_with_bom_crlf_and_bad_lines(tmp_path):
    data = (b"\xef\xbb\xbf0 HEAD\r\n1 CHAR UTF-8\r\ngarbage line\r\n\r\n"
            b"0 @I1@ INDI\r\n1 NAME Jan /Nov\xc3\xa1k/\r\n0 TRLR")
    path = tmp_path / "x.ged"
    path.write_bytes(data)
    doc = parse_gedcom(path)
    assert doc.serialize() == data
    assert doc.encoding == "utf-8"
    assert any(w.startswith("line 3:") for w in doc.warnings)
    assert _record(doc, "@I1@").first("NAME").value == "Jan /Novák/"


def test_cp1250_declared_ansi(tmp_path):
    data = "0 HEAD\n1 CHAR ANSI\n0 @I1@ INDI\n1 NAME Jan /Dvořák/\n0 TRLR\n".encode("cp1250")
    path = tmp_path / "x.ged"
    path.write_bytes(data)
    doc = parse_gedcom(path)
    assert doc.encoding == "cp1250"
    assert _record(doc, "@I1@").first("NAME").value == "Jan /Dvořák/"
    assert doc.serialize() == data


def test_invalid_utf8_falls_back_to_cp1250(tmp_path):
    data = "0 HEAD\n1 CHAR UTF-8\n0 @I1@ INDI\n1 NAME Jan /Dvořák/\n0 TRLR\n".encode("cp1250")
    path = tmp_path / "x.ged"
    path.write_bytes(data)
    doc = parse_gedcom(path)
    assert doc.encoding == "cp1250"
    assert any("not valid UTF-8" in w for w in doc.warnings)
    assert _record(doc, "@I1@").first("NAME").value == "Jan /Dvořák/"


def test_ansel_file(tmp_path):
    path = tmp_path / "x.ged"
    path.write_bytes(b"0 HEAD\n1 CHAR ANSEL\n0 @I1@ INDI\n1 NAME Jan /Nov\xe2ak/\n0 TRLR\n")
    doc = parse_gedcom(path)
    assert doc.encoding == "ansel"
    assert _record(doc, "@I1@").first("NAME").value == "Jan /Novák/"


def test_utf16_with_bom(tmp_path):
    data = "﻿0 HEAD\r\n1 CHAR UNICODE\r\n0 @I1@ INDI\r\n1 NAME Jan /Novák/\r\n0 TRLR\r\n".encode("utf-16-le")
    path = tmp_path / "x.ged"
    path.write_bytes(data)
    doc = parse_gedcom(path)
    assert doc.encoding == "utf-16-le"
    assert _record(doc, "@I1@").first("NAME").value == "Jan /Novák/"
    assert doc.serialize() == data


def test_level_jump_warns_but_keeps_line(tmp_path):
    path = tmp_path / "x.ged"
    path.write_bytes(b"0 HEAD\n0 @I1@ INDI\n2 DATE 1850\n0 TRLR\n")
    doc = parse_gedcom(path)
    assert any("level jumps" in w for w in doc.warnings)
    assert _record(doc, "@I1@").first("DATE").value == "1850"


def test_detect_encoding_defaults_to_cp1250():
    assert detect_encoding(b"0 HEAD\n0 TRLR\n") == ("cp1250", b"")


@pytest.mark.parametrize("path", PRIVATE, ids=lambda p: p.name)
def test_round_trip_private_files(path):
    """Drop real exports into tests/private/ to check they survive byte-for-byte."""
    assert parse_gedcom(path).serialize() == path.read_bytes()
