import pytest

from genea.analysis.gedwrite import CONC_LIMIT, GedEditor, GedWriteError, split_value, text_lines
from genea.ingest.gedcom.parser import parse_gedcom

SMALL = "0 HEAD\r\n1 CHAR UTF-8\r\n0 @I1@ INDI\r\n1 NAME A /B/\r\n1 BIRT\r\n2 DATE 1900\r\n0 TRLR\r\n"


def _doc(tmp_path, text, encoding="utf-8", bom=b""):
    path = tmp_path / "t.ged"
    path.write_bytes(bom + text.encode(encoding))
    return parse_gedcom(path)


def test_split_never_cuts_at_a_space():
    text = ("Žena menom Anna " * 60).strip()
    pieces = split_value(text)
    assert "".join(pieces) == text and len(pieces) > 1
    assert all(len(p) <= CONC_LIMIT for p in pieces)
    for left, right in zip(pieces, pieces[1:]):
        assert not left.endswith(" ") and not right.startswith(" ")


def test_split_keeps_short_text_and_long_words():
    assert split_value("krátky") == ["krátky"]
    assert "".join(split_value("x" * 600)) == "x" * 600


def test_text_lines_use_cont_and_conc():
    assert text_lines(1, "NOTE", "a\nb") == ["1 NOTE a", "2 CONT b"]
    lines = text_lines(2, "NOTE", "y " * 200)
    assert lines[0].startswith("2 NOTE y") and all(line.startswith("3 CONC ") for line in lines[1:])


def test_unchanged_file_is_byte_identical(tmp_path):
    doc = _doc(tmp_path, SMALL, bom=b"\xef\xbb\xbf")
    assert GedEditor(doc).serialize() == doc.path.read_bytes()


def test_insert_after_a_record_uses_the_file_newline(tmp_path):
    editor = GedEditor(_doc(tmp_path, SMALL))
    editor.insert_after(editor.subtree_end(editor.record_index("@I1@")), ["1 NOTE x"])
    assert editor.serialize() == SMALL.replace("2 DATE 1900\r\n", "2 DATE 1900\r\n1 NOTE x\r\n").encode()


def test_subtree_end_and_replace(tmp_path):
    editor = GedEditor(_doc(tmp_path, SMALL))
    assert editor.subtree_end(4) == 5  # 1 BIRT -> its 2 DATE
    editor.owner = "p-1"
    editor.replace(5, "2 DATE 1901")
    assert editor.serialize() == SMALL.replace("1900", "1901").encode()
    assert editor.log == {"p-1": ["-2 DATE 1900", "+2 DATE 1901"]}
    with pytest.raises(GedWriteError, match="twice"):
        editor.replace(5, "2 DATE 1902")


def test_new_records_go_before_trlr(tmp_path):
    editor = GedEditor(_doc(tmp_path, SMALL))
    editor.add_record(["0 @GENEA1@ INDI", "1 NAME C /D/"])
    assert editor.serialize().endswith(b"2 DATE 1900\r\n0 @GENEA1@ INDI\r\n1 NAME C /D/\r\n0 TRLR\r\n")
    assert "@GENEA1@" not in editor.xrefs() and "@I1@" in editor.xrefs()


def test_no_trlr_and_no_final_newline(tmp_path):
    editor = GedEditor(_doc(tmp_path, "0 HEAD\n0 @I1@ INDI\n1 NAME A /B/"))
    editor.add_record(["0 @GENEA1@ INDI"])
    editor.insert_after(2, ["1 SEX M"])
    assert editor.serialize() == b"0 HEAD\n0 @I1@ INDI\n1 NAME A /B/\n1 SEX M\n0 @GENEA1@ INDI\n"


def test_snapshot_and_restore(tmp_path):
    editor = GedEditor(_doc(tmp_path, SMALL))
    snap = editor.snapshot()
    editor.owner = "p-1"
    editor.insert_after(1, ["1 NOTE x"])
    editor.add_record(["0 @GENEA1@ INDI"])
    editor.restore(snap)
    assert editor.serialize() == SMALL.encode() and editor.log == {}


@pytest.mark.parametrize(("header", "encoding"), [("ANSEL", "utf-8"), ("UTF-8", "utf-16-le")])
def test_unwritable_encodings_are_refused(tmp_path, header, encoding):
    bom = b"\xff\xfe" if encoding == "utf-16-le" else b""
    doc = _doc(tmp_path, f"0 HEAD\n1 CHAR {header}\n0 TRLR\n", encoding, bom)
    with pytest.raises(GedWriteError, match="UTF-8"):
        GedEditor(doc)


def test_cp1250_text_that_cannot_be_encoded(tmp_path):
    editor = GedEditor(_doc(tmp_path, "0 HEAD\n1 CHAR ANSI\n0 @I1@ INDI\n1 NAME Ján /Kováč/\n0 TRLR\n", "cp1250"))
    editor.insert_after(3, ["1 NOTE Poznámka"])
    with pytest.raises(GedWriteError, match="cannot be written"):
        editor.insert_after(3, ["1 NOTE Имя"])
    assert editor.serialize().decode("cp1250").endswith("1 NAME Ján /Kováč/\n1 NOTE Poznámka\n0 TRLR\n")


def test_restore_twice_from_the_same_snapshot(tmp_path):
    editor = GedEditor(_doc(tmp_path, SMALL))
    editor.owner = "p-1"
    editor.insert_after(1, ["1 NOTE a"])
    snap = editor.snapshot()
    expected = editor.serialize()
    for n in range(2):
        editor.restore(snap)
        editor.insert_after(1, [f"1 NOTE extra{n}"])
        editor.replace(5, "2 DATE 1901")
        editor.add_record([f"0 @GENEA{n}@ INDI"])
        editor.restore(snap)
        assert editor.serialize() == expected
        assert editor.log == {"p-1": [" 1 CHAR UTF-8", "+1 NOTE a"]}


def test_crlf_no_trlr_and_no_final_newline(tmp_path):
    editor = GedEditor(_doc(tmp_path, "0 HEAD\r\n0 @I1@ INDI\r\n1 NAME A /B/"))
    editor.add_record(["0 @GENEA1@ INDI"])
    assert editor.serialize() == b"0 HEAD\r\n0 @I1@ INDI\r\n1 NAME A /B/\r\n0 @GENEA1@ INDI\r\n"
