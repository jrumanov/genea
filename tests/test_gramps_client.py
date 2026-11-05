import json

import pytest

from fixtures.fake_gramps import FakeGramps
from genea.config import Settings
from genea.gramps.client import (
    Conflict, GrampsClient, GrampsError, TooOld, WrongTree, open_client, tree_for,
)


@pytest.fixture
def fake():
    return FakeGramps()


@pytest.fixture
def client(tmp_path, fake):
    c = GrampsClient("https://gw.test", "me", "pw", tmp_path / "gw.json", fake.transport)
    c.login()
    return c


def test_login_pins_tree(tmp_path, fake):
    c = GrampsClient("https://gw.test", "me", "pw", tmp_path / "gw.json", fake.transport)
    assert c.login() == "tree-a"
    assert json.loads((tmp_path / "gw.json").read_text()) == {"me": "tree-a"}
    info = c.info()
    assert info["tree"] == "tree-a" and info["version"] == "3.22.0" and "EditObject" in info["permissions"]


def test_login_refuses_other_tree(tmp_path, fake):
    (tmp_path / "gw.json").write_text('{"me": "tree-b"}')
    with pytest.raises(WrongTree, match="tree-a.*tree-b|tree-b.*tree-a"):
        GrampsClient("https://gw.test", "me", "pw", tmp_path / "gw.json", fake.transport).login()
    assert ("GET", "/api/people/") not in fake.requests


def test_old_server_refused(tmp_path, fake):
    fake.version = "3.21.1"
    with pytest.raises(TooOld, match="3.22.0"):
        GrampsClient("https://gw.test", "me", "pw", tmp_path / "gw.json", fake.transport).login()


def test_bad_password_is_error(tmp_path, fake):
    with pytest.raises(GrampsError, match="403"):
        GrampsClient("https://gw.test", "me", "nope", tmp_path / "gw.json", fake.transport).login()


def test_refresh_on_401(client, fake):
    h = fake.add("people", {"gramps_id": "I1"})
    fake.fail_on[("GET", "/api/people/")] = 401
    assert client.find_by_gramps_id("people", "I1")["handle"] == h
    assert ("POST", "/api/token/refresh/") in fake.requests


def test_find_and_list_all(client, fake):
    for i in range(1203):
        fake.add("events", {"gramps_id": f"E{i}", "description": "x"})
    assert client.find_by_gramps_id("events", "E7")["gramps_id"] == "E7"
    assert client.find_by_gramps_id("events", "nope") is None
    rows = client.list_all("events", "handle,gramps_id")
    assert len(rows) == 1203 and set(rows[0]) == {"handle", "gramps_id"}


def test_put_412_is_conflict(client, fake):
    h = fake.add("events", {"description": "orig"})
    obj, etag = client.get("events", h)
    fake.objects["events"][h]["description"] = "changed"
    with pytest.raises(Conflict):
        client.update("events", h, obj, etag)


def test_update_and_delete(client, fake):
    h = fake.add("events", {"description": "orig"})
    obj, etag = client.get("events", h)
    obj["description"] = "new"
    changes = client.update("events", h, obj, etag)
    assert changes[0]["type"] == "update" and fake.objects["events"][h]["description"] == "new"
    client.delete("events", h)
    assert h not in fake.objects["events"]


def test_latest_transaction_and_undo(client, fake):
    handle = client.create("events", {"description": "made"})[0]["handle"]
    tid = client.latest_transaction("Event", handle)
    assert tid == fake.history[-1]["id"]
    client.undo(tid)
    assert handle not in fake.objects["events"]


def test_undo_conflict_has_message(client, fake):
    handle = client.create("events", {"description": "made"})[0]["handle"]
    tid = client.latest_transaction("Event", handle)
    fake.objects["events"][handle]["description"] = "edited"
    with pytest.raises(Conflict, match="changed"):
        client.undo(tid)


def test_export(client, fake):
    fake.exports["ged"] = b"0 HEAD\n"
    assert client.export("ged") == b"0 HEAD\n"


def test_other_http_error(client, fake):
    fake.fail_on[("GET", "/api/events/")] = 500
    with pytest.raises(GrampsError, match="GET /api/events/abc: 500 injected 500"):
        client.get("events", "abc")


class _KB:
    def __init__(self, meta_dir, url):
        self.meta_dir = meta_dir
        self.settings = Settings(gramps_web_url=url, gramps_web_trees={"a.ged": "me"})


def test_http_url_refused(tmp_path, fake, monkeypatch):
    monkeypatch.setenv("GENEA_GRAMPS_PASSWORD_ME", "pw")
    with pytest.raises(GrampsError, match="https"):
        open_client(_KB(tmp_path, "http://gw.example.net"), "a.ged", fake.transport)
    open_client(_KB(tmp_path, "http://localhost:5000"), "a.ged", fake.transport)
    assert json.loads((tmp_path / "gramps_web.json").read_text()) == {"me": "tree-a"}


def test_unmapped_ged_refused():
    with pytest.raises(GrampsError, match=r"\[gramps_web_trees\]"):
        tree_for(Settings(gramps_web_trees={"a.ged": "me"}), "b.ged")


def test_login_refuses_missing_tree_claim(tmp_path, fake):
    fake.omit_tree_claim = True
    with pytest.raises(WrongTree, match="no tree claim"):
        GrampsClient("https://gw.test", "me", "pw", tmp_path / "gw.json", fake.transport).login()
    assert not (tmp_path / "gw.json").exists()
    assert ("GET", "/api/people/") not in fake.requests


def test_non_json_success_body_is_grampserror(client, fake):
    fake.html_on.add(("GET", "/api/people/"))
    with pytest.raises(GrampsError, match="not JSON"):
        client.find_by_gramps_id("people", "I1")
