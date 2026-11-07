import json

import pytest

from fixtures.fake_gramps import FakeGramps
from genea.analysis.models import Proposal, WrittenObject
from genea.gramps.client import Conflict, GrampsClient, GrampsError
from genea.gramps.dates import to_gramps_date
from genea.gramps.resolve import Resolver
from genea.gramps.write import WriteLog, write_proposal

GED = "tree.ged"


@pytest.fixture
def fake():
    f = FakeGramps()
    f.add("places", {"handle": "pl1", "gramps_id": "P0", "profile": {"name": "Brno"}})
    f.add("sources", {"handle": "s1", "gramps_id": "S0", "title": "Matrika Brno"})
    f.add("events", {"handle": "e1", "gramps_id": "E1", "type": "Birth", "date": to_gramps_date("1881"),
                     "place": "pl1", "description": ""})
    f.add("people", {"handle": "i1", "gramps_id": "I1", "event_ref_list": [{"ref": "e1"}],
                     "birth_ref_index": 0, "death_ref_index": -1, "parent_family_list": [], "note_list": [],
                     "citation_list": [], "profile": {"name_given": "Jan", "name_surname": "Weiss"}})
    f.add("people", {"handle": "i2", "gramps_id": "I2", "event_ref_list": [], "birth_ref_index": -1,
                     "death_ref_index": -1, "parent_family_list": [], "note_list": [], "citation_list": [],
                     "profile": {"name_given": "Anna", "name_surname": "Weiss"}})
    f.add("families", {"handle": "f1", "gramps_id": "F1", "child_ref_list": []})
    return f


@pytest.fixture
def client(tmp_path, fake):
    c = GrampsClient("https://gw.test", "me", "pw", tmp_path / "gw.json", fake.transport)
    c.login()
    return c


@pytest.fixture
def log(tmp_path):
    return WriteLog(tmp_path / "writes.jsonl")


def lines(log):
    return [json.loads(x) for x in log.path.read_text(encoding="utf-8").splitlines()]


def proposal(operation, payload, anchor=None, pid="p-1"):
    return Proposal(id=pid, author="claude", operation=operation, payload=payload, anchor=anchor, sources=[],
                    rationale="r", created="2026-11-05")


def write(client, p, log, new_people=None):
    target = Resolver(client).resolve(p, new_people or {})
    return write_proposal(client, target, log)


def update_birth():
    return proposal("update_event", {"person_id": f"{GED}#@I1@", "line": 10, "date": "1883"},
                    anchor={"type": "BIRT", "description": None, "date": "1881", "place": "Brno"})


def add_death():
    return proposal("add_event", {"person_id": f"{GED}#@I2@", "type": "DEAT", "date": "1950", "place": "Brno"})


def link():
    return proposal("link_child", {"family_id": f"{GED}#@F1@", "child_id": f"{GED}#@I2@"})


def writes_since(fake, start):
    return [r for r in fake.requests[start:] if r[0] in ("POST", "PUT", "DELETE") and "/token" not in r[1]
            and "/transactions" not in r[1]]


def test_update_event_writes_and_logs(client, fake, log):
    record = write(client, update_birth(), log)
    assert fake.objects["events"]["e1"]["date"]["dateval"][2] == 1883
    tid = fake.history[-1]["id"]
    assert record.objects == [WrittenObject(kind="events", handle="e1", gramps_id="E1", created=False,
                                            transaction=tid)]
    rows = lines(log)
    assert [(r["method"], r["status"]) for r in rows] == [("PUT", 200)]
    assert rows[0]["proposal_id"] == "p-1" and rows[0]["transaction"] == tid and rows[0]["ts"]


def test_add_event_creates_then_links(client, fake, log):
    start = len(fake.requests)
    record = write(client, add_death(), log)
    assert writes_since(fake, start) == [("POST", "/api/events/"), ("PUT", "/api/people/i2")]
    person = fake.objects["people"]["i2"]
    ref = person["event_ref_list"][0]
    event = fake.objects["events"][ref["ref"]]
    assert event["type"] == {"_class": "EventType", "string": "Death"} and event["place"] == "pl1"
    assert ref["role"] == {"_class": "EventRoleType", "string": "Primary"}
    assert person["death_ref_index"] == 0 and person["birth_ref_index"] == -1
    assert [(o.kind, o.created) for o in record.objects] == [("events", True), ("people", False)]


def test_add_person_with_birth(client, fake, log):
    p = proposal("add_person", {"ref": "n1", "given": "Eva", "surname": "Weiss", "sex": "F",
                                "birth_date": "1900", "birth_place": "Brno"})
    record = write(client, p, log)
    handle = record.objects[0].handle
    person = fake.objects["people"][handle]
    assert person["gender"] == 0
    assert person["primary_name"]["first_name"] == "Eva"
    assert person["primary_name"]["surname_list"][0]["surname"] == "Weiss"
    birth = fake.objects["events"][person["event_ref_list"][0]["ref"]]
    assert birth["type"]["string"] == "Birth" and birth["date"]["dateval"][2] == 1900
    assert person["birth_ref_index"] == 0
    assert [(o.kind, o.created) for o in record.objects] == [("people", True), ("events", True), ("people", False)]


def test_link_child_sets_both_sides(client, fake, log):
    write(client, link(), log)
    assert fake.objects["families"]["f1"]["child_ref_list"] == [{"_class": "ChildRef", "ref": "i2"}]
    assert fake.objects["people"]["i2"]["parent_family_list"] == ["f1"]


def test_add_note_and_citation(client, fake, log):
    write(client, proposal("add_note", {"person_id": f"{GED}#@I1@", "text": "hello"}), log)
    note = next(iter(fake.objects["notes"].values()))
    assert note["text"] == {"_class": "StyledText", "string": "hello"}
    assert fake.objects["people"]["i1"]["note_list"] == [note["handle"]]
    write(client, proposal("add_source_citation", {"person_id": f"{GED}#@I1@", "source": "S0", "page": "f. 3",
                                                   "event_type": "BIRT"}, pid="p-2"), log)
    citation = next(iter(fake.objects["citations"].values()))
    assert citation["source_handle"] == "s1" and citation["page"] == "f. 3"
    assert fake.objects["events"]["e1"]["citation_list"] == [citation["handle"]]


def test_412_marks_stale_and_cleans_up(client, fake, log):
    fake.fail_on[("PUT", "/api/people/")] = 412
    with pytest.raises(Conflict, match=r"\(cleaned up\)"):
        write(client, add_death(), log)
    assert set(fake.objects["events"]) == {"e1"}
    rows = lines(log)
    assert [(r["method"], r["status"]) for r in rows] == [("POST", 201), ("PUT", 412), ("DELETE", 200)]
    assert rows[0]["handle"] == rows[2]["handle"] and rows[1]["path"] == "/api/people/i2"
    assert fake.objects["people"]["i2"]["event_ref_list"] == []


def test_cleanup_failure_names_orphan(client, fake, log):
    fake.fail_on[("PUT", "/api/people/")] = 500
    fake.fail_on[("DELETE", "/api/events/")] = 500
    with pytest.raises(GrampsError, match="orphan events E"):
        write(client, add_death(), log)


def test_no_note_written_into_gramps(client, fake, log):
    write(client, update_birth(), log)
    write(client, add_death(), log)
    write(client, link(), log)
    assert fake.objects["notes"] == {}


def test_failure_log_has_post_put_delete(client, fake, log):
    fake.fail_on[("PUT", "/api/people/")] = 500
    with pytest.raises(GrampsError, match=r"500.*\(cleaned up\)"):
        write(client, add_death(), log)
    assert [(r["method"], r["path"].split("/")[2], r["status"]) for r in lines(log)] == [
        ("POST", "events", 201), ("PUT", "people", 500), ("DELETE", "events", 200)]


def test_failed_proposal_restores_updated_family(client, fake, log):
    fake.fail_on[("PUT", "/api/people/")] = 500  # the child's PUT, after the family PUT went through
    with pytest.raises(GrampsError, match=r"500.*\(cleaned up\)"):
        write(client, link(), log)
    assert fake.objects["families"]["f1"]["child_ref_list"] == []
    assert [(r["method"], r["path"].split("/")[2]) for r in lines(log)][-1] == ("PUT", "families")


def _failing_restore(fake, log):
    import httpx

    puts = []

    def handler(request):
        if request.method == "PUT" and request.url.path == "/api/families/f1":
            puts.append(1)
            if len(puts) == 2:  # the first PUT is the write, the second the restore
                return httpx.Response(500, json={"error": {"message": "boom"}})
        return fake._handle(request)

    c = GrampsClient("https://gw.test", "me", "pw", log.path.parent / "gw3.json", httpx.MockTransport(handler))
    c.login()
    return c


def test_restore_failure_named(client, fake, log):
    fake.fail_on[("PUT", "/api/people/")] = 500
    with pytest.raises(GrampsError, match=r"family F1 could not be restored; check it in Gramps") as exc:
        write(_failing_restore(fake, log), link(), log)
    assert "cleaned up" not in str(exc.value)
    assert fake.objects["families"]["f1"]["child_ref_list"] == [{"_class": "ChildRef", "ref": "i2"}]


def test_add_person_update_fails_deletes_both_newest_first(client, fake, log):
    p = proposal("add_person", {"given": "Eva", "surname": "Weiss", "birth_date": "1900"})
    fake.fail_on[("PUT", "/api/people/")] = 500
    with pytest.raises(GrampsError, match=r"\(cleaned up\)$"):
        write(client, p, log)
    assert [(r["method"], r["path"].split("/")[2]) for r in lines(log)] == [
        ("POST", "people"), ("POST", "events"), ("PUT", "people"), ("DELETE", "events"), ("DELETE", "people")]
    assert set(fake.objects["people"]) == {"i1", "i2"} and set(fake.objects["events"]) == {"e1"}


def test_add_person_without_birth(client, fake, log):
    record = write(client, proposal("add_person", {"given": "Eva", "surname": "Weiss"}), log)
    assert [(o.kind, o.created) for o in record.objects] == [("people", True)]
    person = fake.objects["people"][record.objects[0].handle]
    assert person["gender"] == 2 and not person.get("event_ref_list")
    assert set(fake.objects["events"]) == {"e1"}


def test_get_after_post_fails_object_cleaned_up(client, fake, log):
    fake.fail_on[("GET", "/api/events/")] = 500  # the gramps_id read right after the POST
    fake.fail_on[("PUT", "/api/people/")] = 500
    with pytest.raises(GrampsError, match=r"\(cleaned up\)"):
        write(client, add_death(), log)
    assert set(fake.objects["events"]) == {"e1"}
    assert lines(log)[0]["method"] == "POST" and lines(log)[0]["handle"]


def test_orphan_named_by_handle_when_gramps_id_unknown(client, fake, log):
    fake.fail_on[("GET", "/api/events/")] = 500
    fake.fail_on[("PUT", "/api/people/")] = 500
    fake.fail_on[("DELETE", "/api/events/")] = 500
    with pytest.raises(GrampsError, match=r"orphan events [0-9a-f]{32}"):
        write(client, add_death(), log)


def test_transaction_lookup_fails_after_put(client, fake, log):
    fake.fail_on[("GET", "/api/transactions/history/objects/Event/")] = 500
    record = write(client, update_birth(), log)
    assert fake.objects["events"]["e1"]["date"]["dateval"][2] == 1883
    assert record.objects[0].transaction == 0
    assert [(r["method"], r["transaction"]) for r in lines(log)] == [("PUT", None)]


def test_non_json_2xx_body_cleans_up_and_names_error(client, fake, log):
    fake.html_on.add(("PUT", "/api/people/"))  # the PUT is answered 200 with an HTML page
    with pytest.raises(GrampsError, match=r"not JSON.*\(cleaned up\)"):
        write(client, add_death(), log)
    assert set(fake.objects["events"]) == {"e1"}


def test_post_response_without_object_is_grampserror(client, fake, log):
    fake.empty_post_response = True
    with pytest.raises(GrampsError, match="StopIteration"):
        write(client, add_death(), log)


def test_non_gramps_error_after_put_names_updated_object(client, fake, log):
    import httpx

    def handler(request):
        if request.method == "GET" and request.url.path == "/api/people/i2" and ("PUT", "/api/families/f1") in fake.requests:
            raise RuntimeError("boom")
        return fake._handle(request)

    c = GrampsClient("https://gw.test", "me", "pw", log.path.parent / "gw2.json", httpx.MockTransport(handler))
    c.login()
    with pytest.raises(GrampsError, match=r"RuntimeError: boom.*\(cleaned up\)"):
        write(c, link(), log)
    assert fake.objects["families"]["f1"]["child_ref_list"] == []


def test_keyboard_interrupt_is_not_swallowed(client, fake, log, monkeypatch):
    monkeypatch.setattr(client, "update", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        write(client, add_death(), log)
