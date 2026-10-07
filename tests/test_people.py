import pytest

from genea.tools.base import ToolError
from genea.tools.people import find_person, get_person, relatives, timeline

T = "tree.ged#"


def test_find_person_by_name(tools):
    ids = {c.id for c in find_person(tools, "Jan Novák").candidates}
    assert ids == {T + "@I3@", T + "@I12@"}


def test_find_person_ranks_by_birth_year(tools):
    assert find_person(tools, "jan novak", born_around=1900).candidates[0].id == T + "@I12@"
    assert find_person(tools, "jan novak", born_around=1850).candidates[0].id == T + "@I3@"


def test_find_person_by_variant(tools):
    result = find_person(tools, "Joseph Nowak")
    assert [c.id for c in result.candidates] == [T + "@I1@"]
    assert result.sources[0].line == 7


def test_find_person_place_boost(tools):
    assert find_person(tools, "Marie", place="Tišnov").candidates[0].id == T + "@I16@"


def test_find_person_empty_name(tools):
    with pytest.raises(ToolError):
        find_person(tools, "  ")


def test_get_person(tools):
    d = get_person(tools, T + "@I3@")
    assert d.name == "Jan Novák"
    assert [p.id for p in d.parents] == [T + "@I1@", T + "@I2@"]
    assert [p.id for p in d.spouses] == [T + "@I6@"]
    assert [p.id for p in d.children] == [T + "@I7@", T + "@I8@", T + "@I9@"]
    assert d.families_as_spouse == [T + "@F2@"]
    assert d.families_as_child == [T + "@F1@"]
    birth = next(e for e in d.events if e.type == "BIRT")
    assert birth.citations[0]["page"] == "fol. 23"
    assert birth.source.file == "tree.ged" and birth.source.line is not None
    assert any(e.type == "MARR" and e.place == "Tišnov" for e in d.events)


def test_get_person_short_ids(tools):
    assert get_person(tools, "I3").id == T + "@I3@"
    assert get_person(tools, "@I3@").id == T + "@I3@"


def test_get_person_unknown(tools):
    with pytest.raises(ToolError, match="find_person"):
        get_person(tools, T + "@I999@")


def test_ancestors(tools):
    result = relatives(tools, T + "@I17@", "ancestors", depth=3)
    by_generation = {}
    for rel in result.relatives:
        by_generation.setdefault(rel.generation, set()).add(rel.person.id)
    assert by_generation == {1: {T + "@I12@", T + "@I16@"},
                             2: {T + "@I7@", T + "@I10@"},
                             3: {T + "@I3@", T + "@I6@"}}


def test_simple_relations(tools):
    assert [r.person.id for r in relatives(tools, T + "@I7@", "siblings").relatives] == [T + "@I8@", T + "@I9@"]
    assert [r.person.id for r in relatives(tools, T + "@I8@", "spouses").relatives] == [T + "@I11@"]
    descendants = {r.person.id for r in relatives(tools, T + "@I1@", "descendants", depth=2).relatives}
    assert descendants == {T + x for x in ("@I3@", "@I4@", "@I5@", "@I7@", "@I8@", "@I9@")}


def test_bad_relation(tools):
    with pytest.raises(ToolError, match="relation"):
        relatives(tools, T + "@I7@", "cousins")


def test_timeline(tools):
    t = timeline(tools, T + "@I3@")
    seen = {(e.role, e.type, e.person_id) for e in t.entries}
    assert ("self", "BIRT", T + "@I3@") in seen
    assert ("self", "MARR", T + "@I3@") in seen
    assert ("parent", "MARR", T + "@I1@") in seen
    assert ("child", "BIRT", T + "@I7@") in seen
    dated = [e.date_from for e in t.entries if e.date_from]
    assert dated == sorted(dated)
