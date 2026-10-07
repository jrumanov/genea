from pathlib import Path

from genea.ingest.gedcom.mapper import map_gedcom, split_name
from genea.ingest.gedcom.parser import parse_gedcom

FIXTURE = Path(__file__).parent / "fixtures" / "kb_sample" / "tree.ged"
DATA = map_gedcom(parse_gedcom(FIXTURE))
PERSONS = {p.xref: p for p in DATA.persons}
FAMILIES = {f.xref: f for f in DATA.families}


def _events(owner, tag):
    return [e for e in DATA.events if e.owner_xref == owner and e.type == tag]


def test_counts():
    assert len(PERSONS) == 20
    assert len(FAMILIES) == 6
    assert DATA.warnings == []


def test_person_names_variants_notes():
    josef = PERSONS["@I1@"]
    assert (josef.given, josef.surname, josef.sex) == ("Josef", "Novák", "M")
    assert josef.name_variants == ["Josef Novák", "Joseph Nowak"]
    assert josef.notes == "Sedlák v Brně, uváděn v kronice obce.\nDruhý řádek poznámky."
    assert josef.line_no == 7


def test_event_with_citation():
    [birth] = _events("@I3@", "BIRT")
    assert birth.date_raw == "5 MAY 1846"
    assert (birth.date.date_from, birth.date.date_to) == ("1846-05-05", "1846-05-05")
    assert birth.place == "Brno"
    assert birth.citations == [
        {"source": "Matrika narozených Brno 1840–1860", "xref": "@S1@", "page": "fol. 23"}
    ]


def test_occupation_description():
    [occu] = _events("@I1@", "OCCU")
    assert occu.description == "sedlák"


def test_families_children_and_pedigree():
    f1 = FAMILIES["@F1@"]
    assert (f1.husband_xref, f1.wife_xref) == ("@I1@", "@I2@")
    assert f1.children == [("@I3@", "birth"), ("@I4@", "birth"), ("@I5@", "adopted")]
    assert FAMILIES["@F6@"].wife_xref is None


def test_family_event():
    [marr] = _events("@F2@", "MARR")
    assert marr.date.date_from == "1870-02-10"
    assert marr.place == "Tišnov"


def test_split_name():
    assert split_name("Jan /Novák/") == ("Jan", "Novák")
    assert split_name("Jan") == ("Jan", "")
    assert split_name("/Novák/") == ("", "Novák")
    assert split_name("") == ("", "")
