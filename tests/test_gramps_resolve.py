import pytest

from fixtures.fake_gramps import FakeGramps
from genea.analysis.models import Proposal
from genea.tools.models import SourceRef
from genea.gramps.client import GrampsClient
from genea.gramps.dates import to_gramps_date
from genea.gramps.resolve import TAG_TO_GRAMPS, Resolver, Stale, gramps_id
from genea.ingest.gedcom.mapper import INDIVIDUAL_EVENT_TAGS

GED = "tree.ged"


@pytest.fixture
def fake():
    f = FakeGramps()
    f.add("places", {"handle": "pl1", "gramps_id": "P0", "profile": {"name": "Brno"}})
    f.add("sources", {"handle": "s1", "gramps_id": "S0", "title": "Matrika Brno"})
    f.add("events", {"handle": "e1", "gramps_id": "E1", "type": "Birth", "date": to_gramps_date("1881"),
                     "place": "pl1", "description": ""})
    f.add("people", {"handle": "i1", "gramps_id": "I1", "event_ref_list": [{"ref": "e1"}],
                     "parent_family_list": [], "profile": {"name_given": "Jan", "name_surname": "Weiss",
                                                           "birth": {"date": "1881-00-00"}}})
    f.add("people", {"handle": "i2", "gramps_id": "I2", "event_ref_list": [], "parent_family_list": [],
                     "profile": {"name_given": "Anna", "name_surname": "Weiss", "birth": {}}})
    f.add("families", {"handle": "f1", "gramps_id": "F1", "child_ref_list": [{"ref": "i1"}]})
    return f


@pytest.fixture
def resolver(tmp_path, fake):
    c = GrampsClient("https://gw.test", "me", "pw", tmp_path / "gw.json", fake.transport)
    c.login()
    return Resolver(c)


def proposal(operation, payload, anchor=None, pid="p-1"):
    return Proposal(id=pid, author="claude", operation=operation, payload=payload, anchor=anchor, sources=[],
                    rationale="r", created="2026-11-05")


def update_birth(date="1882"):
    return proposal("update_event", {"person_id": f"{GED}#@I1@", "line": 10, "date": date},
                    anchor={"type": "BIRT", "description": None, "date": "1881", "place": "Brno"})


def test_gramps_id():
    assert gramps_id("tree.ged#@I0012@") == "I0012"


def test_tag_map_covers_individual_events():
    assert INDIVIDUAL_EVENT_TAGS - set(TAG_TO_GRAMPS) == {"EVEN"}


def test_update_event_target(resolver):
    t = resolver.resolve(update_birth(), {})
    assert t.reads["event"][1]["gramps_id"] == "E1"
    assert t.reads["event"][2] and t.gramps_date["dateval"] == [0, 0, 1882, False]


def test_update_event_stale_when_user_changed_date(resolver, fake):
    fake.objects["events"]["e1"]["date"] = to_gramps_date("1882")
    with pytest.raises(Stale, match="1882.*1881"):
        resolver.resolve(update_birth("1883"), {})


def test_update_event_stale_when_user_changed_place(resolver, fake):
    fake.add("places", {"handle": "pl2", "gramps_id": "P1", "profile": {"name": "Praha"}})
    fake.objects["events"]["e1"]["place"] = "pl2"
    with pytest.raises(Stale, match="Praha"):
        resolver.resolve(update_birth(), {})


def test_update_event_stale_when_twice(resolver, fake):
    fake.add("events", {"handle": "e2", "gramps_id": "E2", "type": "Birth", "date": to_gramps_date("1881"),
                        "place": "pl1", "description": ""})
    fake.objects["people"]["i1"]["event_ref_list"].append({"ref": "e2"})
    with pytest.raises(Stale, match="more than once"):
        resolver.resolve(update_birth(), {})


def test_missing_person_is_stale(resolver):
    p = proposal("add_note", {"person_id": f"{GED}#@I9@", "text": "x"})
    with pytest.raises(Stale, match="no longer in Gramps"):
        resolver.resolve(p, {})


def test_place_must_exist_to_update_an_event(resolver):
    p = update_birth()
    p.payload["place"] = "Bratislava"
    with pytest.raises(ValueError, match="not in Gramps"):
        resolver.resolve(p, {})


def test_unknown_place_of_a_new_event_is_new(resolver):
    p = proposal("add_event", {"person_id": f"{GED}#@I1@", "type": "DEAT", "date": "1950", "place": "Bratislava"})
    t = resolver.resolve(p, {})
    assert t.new_place == "Bratislava" and t.place_handle is None


def test_ambiguous_place_not_created(resolver, fake):
    fake.add("places", {"handle": "pl2", "gramps_id": "P2", "profile": {"name": "Brno"}})
    p = proposal("add_event", {"person_id": f"{GED}#@I1@", "type": "DEAT", "date": "1950", "place": "Brno"})
    with pytest.raises(ValueError, match="not unique"):
        resolver.resolve(p, {})


def test_place_by_gramps_id(resolver):
    p = proposal("add_event", {"person_id": f"{GED}#@I1@", "type": "DEAT", "date": "1950", "place": "P0"})
    t = resolver.resolve(p, {})
    assert t.place_handle == "pl1" and t.reads["person"][1]["handle"] == "i1"


def test_add_event_duplicate_is_stale(resolver):
    p = proposal("add_event", {"person_id": f"{GED}#@I1@", "type": "BIRT", "date": "1881"})
    with pytest.raises(Stale, match="already has"):
        resolver.resolve(p, {})


def test_source_citation_on_event(resolver):
    p = proposal("add_source_citation", {"person_id": f"{GED}#@I1@", "source": "Matrika Brno", "event_type": "BIRT"})
    t = resolver.resolve(p, {})
    assert t.source_handle == "s1" and t.reads["owner"][0] == "events" and t.reads["owner"][1]["handle"] == "e1"


def test_source_citation_needs_one_event(resolver):
    p = proposal("add_source_citation", {"person_id": f"{GED}#@I1@", "source": "S0", "event_type": "DEAT"})
    with pytest.raises(Stale, match="exactly one"):
        resolver.resolve(p, {})


def test_link_child_already_linked_is_stale(resolver):
    p = proposal("link_child", {"family_id": f"{GED}#@F1@", "child_id": f"{GED}#@I1@"})
    with pytest.raises(Stale, match="already a child"):
        resolver.resolve(p, {})


def test_link_child_other_parent_family_is_stale(resolver, fake):
    fake.objects["people"]["i2"]["parent_family_list"] = ["f9"]
    p = proposal("link_child", {"family_id": f"{GED}#@F1@", "child_id": f"{GED}#@I2@"})
    with pytest.raises(Stale, match="another parent family"):
        resolver.resolve(p, {})


def test_link_child_target(resolver):
    p = proposal("link_child", {"family_id": f"{GED}#@F1@", "child_id": f"{GED}#@I2@"})
    assert resolver.resolve(p, {}).reads["family"][1]["handle"] == "f1"


def test_link_child_new_person_needs_written_person(resolver):
    p = proposal("link_child", {"family_id": f"{GED}#@F1@", "child_id": "proposal:p-3"})
    with pytest.raises(Stale, match="p-3 was not written"):
        resolver.resolve(p, {})
    assert resolver.resolve(p, {"p-3": "i2"}).reads["family"][1]["handle"] == "f1"


def test_add_person_duplicate_is_stale(resolver):
    p = proposal("add_person", {"given": "Jan", "surname": "Weiss", "birth_date": "1881"})
    with pytest.raises(Stale, match="already has"):
        resolver.resolve(p, {})


def test_add_person_other_birth_is_not_duplicate(resolver):
    p = proposal("add_person", {"given": "Jan", "surname": "Weiss", "birth_date": "1885", "birth_place": "Brno"})
    t = resolver.resolve(p, {})
    assert t.reads == {} and t.place_handle == "pl1"


def test_unrepresentable_date_is_value_error(resolver):
    p = proposal("add_event", {"person_id": f"{GED}#@I1@", "type": "DEAT", "date": "@#DHEBREW@ 5650"})
    with pytest.raises(ValueError):
        resolver.resolve(p, {})


def test_resolve_rerun_note_stale(resolver, fake):
    p = proposal("add_note", {"person_id": f"{GED}#@I2@", "text": "Mlynár"})
    p.sources = [SourceRef(file="docs/a.pdf", page=1)]
    fake.add("notes", {"handle": "n1", "gramps_id": "N1",
                       "text": {"_class": "StyledText", "string": "Mlynár \n Source:  [docs/a.pdf p.1]"}})
    fake.objects["people"]["i2"]["note_list"] = ["n1"]
    with pytest.raises(Stale, match="already in Gramps"):
        resolver.resolve(p, {})
