from genea.changes.export import export_gedcom_changes
from genea.changes.store import ChangeStore
from genea.ingest.gedcom.parser import parse_gedcom
from genea.tools.curate import propose_change
from genea.tools.models import SourceRef


def test_export_nothing_pending(tools):
    assert export_gedcom_changes(tools.kb, ChangeStore(tools.kb)) is None


def test_export(tools):
    event = propose_change(tools, "gedcom", "tree.ged", "add_event",
                           {"person_id": "tree.ged#@I9@", "type": "BAPM", "date": "1875", "place": "Brno"},
                           "The register says he was baptized in 1875",
                           [SourceRef(file="docs/matrika.pdf", page=2)])
    propose_change(tools, "gedcom", "tree.ged", "add_person",
                   {"given": "Karel", "surname": "Novák", "sex": "M", "birth_date": "1897"}, "Mentioned in a letter")
    propose_change(tools, "gedcom", "tree.ged", "add_note",
                   {"person_id": "tree.ged#@I1@", "text": "Line one\nLine two"}, "Chronicle")
    propose_change(tools, "gedcom", "tree.ged", "link_child",
                   {"family_id": "tree.ged#@F6@", "child_id": "tree.ged#@I14@"}, "Test link")
    propose_change(tools, "image_xmp", "photos/dum.jpg", "add_keywords", {"keywords": ["dům"]}, "Not exported")

    md_path, ged_path = export_gedcom_changes(tools.kb, ChangeStore(tools.kb))
    md = md_path.read_text(encoding="utf-8")
    assert event.id in md
    assert "- [ ] Add BAPM to tree.ged#@I9@: date 1875, place Brno" in md
    assert "[docs/matrika.pdf p.2]" in md
    assert "genea changes apply <id> --manual" in md
    assert "dům" not in md

    lines = ged_path.read_text(encoding="utf-8").splitlines()
    assert (lines[0], lines[-1]) == ("0 HEAD", "0 TRLR")
    for expected in ("0 @I9@ INDI", "1 BAPM", "2 DATE 1875", "2 PLAC Brno", "0 @GENEA2@ INDI",
                     "1 NAME Karel /Novák/", "1 NOTE Line one", "2 CONT Line two",
                     "0 @F6@ FAM", "1 CHIL @I14@"):
        assert expected in lines
    assert parse_gedcom(ged_path).warnings == []
