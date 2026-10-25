import pytest

from genea.index.db import open_index
from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.names import married_surnames
from genea.tools.people import find_person, get_person


@pytest.mark.parametrize(("husband", "expected"), [
    ("Novák", ["novakova", "novak"]),
    ("Novotný", ["novotna", "novotny"]),
    ("Svoboda", ["svobodova", "svoboda"]),
    ("Krejčí", ["krejci"]),
    ("Weiss", ["weissova", "weiss"]),
    ("", []),
])
def test_married_surnames(husband, expected):
    assert married_surnames(husband) == expected


def test_find_person_by_married_name(tools):
    # the sample tree also has a recorded Anna Nováková (@I13@); the wife of Josef Novák is found as well
    assert {c.id for c in find_person(tools, "Anna Nováková").candidates} >= {"tree.ged#@I2@", "tree.ged#@I13@"}


def test_married_name_is_not_presented_as_recorded(tools):
    person = get_person(tools, "tree.ged#@I2@")
    assert not any("nov" in v.lower() for v in person.name_variants)


def test_married_name_links_a_document_at_lower_confidence(kb):
    (kb.root / "docs" / "rodina.txt").write_text("Anna Nováková bydlela v Tišnově.", encoding="utf-8")
    index_kb(kb, FakeEmbedder())
    conn = open_index(kb, FakeEmbedder())
    rows = conn.execute(
        "SELECT l.person_xref, l.confidence FROM links l JOIN chunks c ON c.id = l.source_id "
        "JOIN files f ON f.id = c.file_id WHERE l.source_kind = 'chunk' AND f.path = 'docs/rodina.txt'").fetchall()
    assert {(r[0], r[1]) for r in rows} == {("@I2@", 0.8), ("@I13@", 1.0)}
