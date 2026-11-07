import json
import subprocess
from dataclasses import replace

import httpx
import pytest
from fixtures.analysis_tree import DOCS, TREE
from fixtures.fake_gramps import FakeGramps

from genea.analysis import api, store
from genea.gramps.write import write_proposal
from genea.analysis.apply import apply_analysis, prepare, snapshot_only, undo_proposal, written_but_missing
from genea.gramps import snapshot
from genea.gramps.dates import to_gramps_date
from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.kb import KnowledgeBase, init_kb
from genea.tools.base import ToolError, open_tools

GED = "fam/tree.ged"
CITE = ["[docs/zapis.txt]"]
PR_URL = "https://github.com/x/fam/pull/1"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout


def _repo(root):
    origin = root.parent / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    work = root / "fam"
    work.mkdir(parents=True)
    git(work, "init", "-b", "main")
    git(work, "config", "user.name", "Test")
    git(work, "config", "user.email", "t@example.com")
    git(work, "config", "core.autocrlf", "false")
    (work / "tree.ged").write_bytes(TREE.encode("utf-8"))
    git(work, "add", "-A")
    git(work, "commit", "-m", "init")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-u", "origin", "main")
    return work, origin


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path / "kb"
    repo, origin = _repo(root)
    for rel, text in DOCS.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    base = init_kb(root)
    kb = KnowledgeBase(root, replace(base.settings, gramps_web_url="https://gw.test",
                                     gramps_web_trees={GED: "me"}))
    index_kb(kb, FakeEmbedder())
    monkeypatch.setenv("GENEA_GRAMPS_PASSWORD_ME", "pw")
    ctx = open_tools(kb, FakeEmbedder())
    yield ctx, repo, origin
    ctx.conn.close()


@pytest.fixture
def ctx(setup):
    return setup[0]


@pytest.fixture
def repo(setup):
    return setup[1]


class _LiveExports(dict):
    """Exports that follow the fake's objects, so before and after differ when something was written."""

    def __init__(self, fake):
        super().__init__()
        self.fake = fake

    def get(self, key, default=None):
        state = {k: self.fake.objects[k] for k in ("people", "events", "families", "notes", "citations")}
        return (f"0 HEAD\n1 SOUR {key}\n" + json.dumps(state, sort_keys=True) + "\n0 TRLR\n").encode()


def _handle(xref):
    return "h" + xref.strip("@")


def _event_handle(xref, tag):
    return "e" + xref.strip("@") + tag


@pytest.fixture
def fake(ctx):
    f = FakeGramps()
    places = {}
    for row in ctx.conn.execute("SELECT DISTINCT place FROM events WHERE place IS NOT NULL AND place != ''"):
        places[row["place"]] = f.add("places", {"handle": f"pl{len(places)}", "gramps_id": f"P{len(places)}",
                                                "profile": {"name": row["place"]}})
    parents: dict[str, list[str]] = {}
    for fam in ctx.conn.execute("SELECT xref FROM families WHERE ged_file = ?", (GED,)).fetchall():
        kids = [r["child_xref"] for r in ctx.conn.execute(
            "SELECT child_xref FROM family_children WHERE ged_file = ? AND family_xref = ?", (GED, fam["xref"]))]
        f.add("families", {"handle": _handle(fam["xref"]), "gramps_id": fam["xref"].strip("@"),
                           "child_ref_list": [{"ref": _handle(k)} for k in kids]})
        for k in kids:
            parents.setdefault(k, []).append(_handle(fam["xref"]))
    for person in ctx.conn.execute("SELECT xref, given, surname FROM persons WHERE ged_file = ?", (GED,)).fetchall():
        refs = []
        for ev in ctx.conn.execute("SELECT type, date_raw, place FROM events WHERE ged_file = ? AND owner_xref = ? "
                                   "AND type = 'BIRT'", (GED, person["xref"])):
            h = f.add("events", {"handle": _event_handle(person["xref"], "BIRT"), "type": "Birth",
                                 "date": to_gramps_date(ev["date_raw"]), "place": places.get(ev["place"], ""),
                                 "description": ""})
            refs.append({"ref": h})
        f.add("people", {"handle": _handle(person["xref"]), "gramps_id": person["xref"].strip("@"),
                         "event_ref_list": refs, "birth_ref_index": 0 if refs else -1, "death_ref_index": -1,
                         "parent_family_list": parents.get(person["xref"], []), "note_list": [],
                         "citation_list": [], "profile": {"name_given": person["given"],
                                                          "name_surname": person["surname"]}})
    f.exports = _LiveExports(f)
    return f


@pytest.fixture
def prs():
    return []


@pytest.fixture
def run(prs):
    def runner(args, cwd):
        if args[0] == "gh":
            if args[1] == "--version":
                return subprocess.CompletedProcess(args, 0, "gh version 2\n", "")
            prs.append(args)
            return subprocess.CompletedProcess(args, 0, f"Creating pull request\n{PR_URL}\n", "")
        return snapshot.default_runner(args, cwd)

    return runner


def _line(ctx, xref, tag="BIRT"):
    return ctx.conn.execute("SELECT line_no FROM events WHERE ged_file = ? AND owner_xref = ? AND type = ?",
                            (GED, xref, tag)).fetchone()["line_no"]


def _analysis(ctx):
    return api.analyze(ctx, "family", f"{GED}#@F1@").name


def _update(ctx, name, xref, new_date):
    return api.propose(ctx, name, "update_event", {"person_id": f"{GED}#{xref}", "line": _line(ctx, xref),
                                                   "date": new_date}, CITE, "the register says so").proposal_id


def _apply(ctx, name, fake, run, transport=None):
    plan = prepare(ctx, name, run=run)
    return apply_analysis(ctx, plan, transport or fake.transport, run)


def _proposal(ctx, name, pid):
    return next(p for p in store.load(ctx.kb, name).proposals if p.id == pid)


def _body(prs):
    return prs[-1][prs[-1].index("--body") + 1]


def test_apply_writes_and_opens_pr(ctx, fake, run, prs, repo):
    name = _analysis(ctx)
    pid = _update(ctx, name, "@I3@", "2 JAN 1878")
    api.build_iteration(ctx, name)
    result = _apply(ctx, name, fake, run)
    assert result.written == [pid] and result.stale == {} and result.failed == {}
    assert result.pr == PR_URL
    assert fake.objects["events"][_event_handle("@I3@", "BIRT")]["date"] == to_gramps_date("2 JAN 1878")
    p = _proposal(ctx, name, pid)
    assert p.status == "written" and p.written.pr == PR_URL
    assert p.written.objects[0].handle == _event_handle("@I3@", "BIRT")
    assert prs[-1][prs[-1].index("--title") + 1] == f"genea: {name} (1 changes)"
    body = _body(prs)
    assert f"- {pid}: Update the event" in body and "Why: the register says so" in body
    assert "Evidence: [docs/zapis.txt]" in body
    assert git(repo, "branch", "--show-current").strip() == "main"
    lines = (store.analysis_dir(ctx.kb, name) / "writes.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["method"] == "PUT"


def test_prepare_cuts_hunks_by_proposal(ctx, run):
    name = _analysis(ctx)
    p1 = _update(ctx, name, "@I3@", "2 JAN 1878")
    p2 = _update(ctx, name, "@I6@", "1821")  # far enough from I3 to get a hunk of its own
    api.build_iteration(ctx, name)
    plan = prepare(ctx, name, run=run)
    assert "+2 DATE 2 JAN 1878" in plan.diff_hunks[p1] and "+2 DATE 1821" in plan.diff_hunks[p2]
    assert "1821" not in plan.diff_hunks[p1]
    assert [p.id for p in prepare(ctx, name, only=[p2], run=run).proposals] == [p2]
    with pytest.raises(ToolError, match="p-9"):
        prepare(ctx, name, only=["p-9"], run=run)


def test_apply_refuses_outdated_build(ctx, fake, run, repo):
    name = _analysis(ctx)
    with pytest.raises(ToolError, match="analysis build"):
        prepare(ctx, name, run=run)  # no build yet
    _update(ctx, name, "@I3@", "2 JAN 1878")
    api.build_iteration(ctx, name)
    (repo / "tree.ged").write_bytes(TREE.replace("2 DATE 1910", "2 DATE 1911").encode("utf-8"))
    with pytest.raises(ToolError, match="analysis build"):
        prepare(ctx, name, run=run)
    assert fake.requests == []


def test_apply_refuses_new_proposal_since_build(ctx, fake, run):
    name = _analysis(ctx)
    _update(ctx, name, "@I3@", "2 JAN 1878")
    api.build_iteration(ctx, name)
    _update(ctx, name, "@I4@", "2 JUN 1878")
    with pytest.raises(ToolError, match="analysis build"):
        prepare(ctx, name, run=run)
    assert fake.requests == []


def test_apply_refuses_dirty_or_behind_clone(ctx, fake, run, repo, setup, tmp_path):
    name = _analysis(ctx)
    _update(ctx, name, "@I3@", "2 JAN 1878")
    api.build_iteration(ctx, name)

    (repo / "stray.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ToolError, match="not clean"):
        prepare(ctx, name, run=run)
    (repo / "stray.txt").unlink()

    other = tmp_path / "other"
    git(tmp_path, "clone", str(setup[2]), str(other))
    git(other, "config", "user.name", "Test")
    git(other, "config", "user.email", "t@example.com")
    (other / "README").write_text("x", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "commit", "-m", "elsewhere")
    git(other, "push", "origin", "main")
    with pytest.raises(ToolError, match="behind"):
        prepare(ctx, name, run=run)
    git(repo, "pull")

    def no_gh(args, cwd):
        if args[0] == "gh":
            raise OSError("not found")
        return run(args, cwd)

    with pytest.raises(ToolError, match="gh"):
        prepare(ctx, name, run=no_gh)
    plan = prepare(ctx, name, run=run)
    with pytest.raises(ToolError, match="gh"):
        apply_analysis(ctx, plan, fake.transport, no_gh)  # checked again before writing
    assert fake.requests == []


def test_stale_and_written_mixed(ctx, fake, run, prs):
    name = _analysis(ctx)
    p1 = _update(ctx, name, "@I3@", "2 JAN 1878")
    p2 = _update(ctx, name, "@I4@", "2 JUN 1878")
    api.build_iteration(ctx, name)
    fake.objects["events"][_event_handle("@I3@", "BIRT")]["date"] = to_gramps_date("5 JAN 1878")
    result = _apply(ctx, name, fake, run)
    assert result.written == [p2] and list(result.stale) == [p1]
    assert "5 JAN 1878" in result.stale[p1]
    body = _body(prs)
    assert "Not written:" in body and f"- {p1} (stale): Gramps has" in body
    assert _proposal(ctx, name, p1).status == "stale"


def test_nothing_written_no_pr(ctx, fake, run, prs, repo):
    name = _analysis(ctx)
    p1 = _update(ctx, name, "@I3@", "2 JAN 1878")
    api.build_iteration(ctx, name)
    fake.objects["events"][_event_handle("@I3@", "BIRT")]["date"] = to_gramps_date("5 JAN 1878")
    result = _apply(ctx, name, fake, run)
    assert result.pr is None and result.written == [] and p1 in result.stale
    assert prs == []
    assert git(repo, "branch", "--list").split() == ["*", "main"]


def _new_child(ctx, name):
    person = api.propose(ctx, name, "add_person", {"given": "Mária", "surname": "Weiss", "sex": "F",
                                                   "birth_date": "1883"}, CITE, "why").proposal_id
    link = api.propose(ctx, name, "link_child", {"family_id": f"{GED}#@F1@", "child_id": f"proposal:{person}"},
                       CITE, "why").proposal_id
    return person, link


def test_link_child_to_new_person_in_same_apply(ctx, fake, run):
    name = _analysis(ctx)
    person, link = _new_child(ctx, name)
    api.build_iteration(ctx, name)
    result = _apply(ctx, name, fake, run)
    assert result.written == [person, link]
    handle = next(o.handle for o in _proposal(ctx, name, person).written.objects if o.kind == "people")
    assert {"_class": "ChildRef", "ref": handle} in fake.objects["families"][_handle("@F1@")]["child_ref_list"]


def test_link_child_goes_stale_with_its_person(ctx, fake, run):
    name = _analysis(ctx)
    person, link = _new_child(ctx, name)
    api.build_iteration(ctx, name)
    fake.add("people", {"handle": "dup", "gramps_id": "I99", "profile": {
        "name_given": "Mária", "name_surname": "Weiss", "birth": {"date": "1883"}}})
    result = _apply(ctx, name, fake, run)
    assert result.written == [] and set(result.stale) == {person, link}
    assert "was not written" in result.stale[link]


def test_saved_after_each_proposal(ctx, fake, run, monkeypatch):
    name = _analysis(ctx)
    p1 = _update(ctx, name, "@I3@", "2 JAN 1878")
    p2 = _update(ctx, name, "@I4@", "2 JUN 1878")
    api.build_iteration(ctx, name)

    real = write_proposal

    def crashing(client, target, log):
        if target.proposal.id == p2:
            raise RuntimeError("crash")
        return real(client, target, log)

    monkeypatch.setattr("genea.analysis.apply.write_proposal", crashing)
    with pytest.raises(ToolError, match=f"after {p1} were written.*genea snapshot {GED}"):
        _apply(ctx, name, fake, run)
    assert _proposal(ctx, name, p1).status == "written"
    assert _proposal(ctx, name, p2).status == "active"


def test_undo_restores_and_reactivates(ctx, fake, run, prs):
    name = _analysis(ctx)
    pid = _update(ctx, name, "@I3@", "2 JAN 1878")
    api.build_iteration(ctx, name)
    _apply(ctx, name, fake, run)
    result = undo_proposal(ctx, name, pid, fake.transport, run)
    assert result.failed == {} and result.pr == PR_URL
    assert fake.objects["events"][_event_handle("@I3@", "BIRT")]["date"] == to_gramps_date("1 JAN 1878")
    p = _proposal(ctx, name, pid)
    assert p.status == "active" and p.written is None
    assert prs[-1][prs[-1].index("--title") + 1] == f"genea: undo {name} {pid}"


def test_undo_new_person_newest_first(ctx, fake, run):
    name = _analysis(ctx)
    person, link = _new_child(ctx, name)
    api.build_iteration(ctx, name)
    people = set(fake.objects["people"])
    _apply(ctx, name, fake, run)
    with pytest.raises(ToolError, match=f"undo {link} first"):
        undo_proposal(ctx, name, person, fake.transport, run)
    assert undo_proposal(ctx, name, link, fake.transport, run).failed == {}
    assert fake.objects["families"][_handle("@F1@")]["child_ref_list"] == [
        {"ref": _handle(x)} for x in ("@I3@", "@I4@", "@I5@")]
    assert undo_proposal(ctx, name, person, fake.transport, run).failed == {}
    assert set(fake.objects["people"]) == people and len(fake.objects["events"]) == 6


def test_undo_conflict_leaves_written(ctx, fake, run):
    name = _analysis(ctx)
    pid = _update(ctx, name, "@I3@", "2 JAN 1878")
    api.build_iteration(ctx, name)
    _apply(ctx, name, fake, run)
    fake.objects["events"][_event_handle("@I3@", "BIRT")]["description"] = "edited by hand"
    result = undo_proposal(ctx, name, pid, fake.transport, run)
    assert f"undo event {_event_handle('@I3@', 'BIRT')}" in result.failed[pid]
    assert result.pr is None
    assert _proposal(ctx, name, pid).status == "written"


def test_undo_unknown_transaction_is_refused(ctx, fake, run):
    name = _analysis(ctx)
    pid = _update(ctx, name, "@I3@", "2 JAN 1878")
    api.build_iteration(ctx, name)
    _apply(ctx, name, fake, run)
    analysis = store.load(ctx.kb, name)
    next(p for p in analysis.proposals if p.id == pid).written.objects[0].transaction = 0
    store.save(ctx.kb, analysis)
    start = len(fake.requests)
    with pytest.raises(ToolError, match="by hand"):
        undo_proposal(ctx, name, pid, fake.transport, run)
    assert fake.requests[start:] == []
    assert _proposal(ctx, name, pid).status == "written"


def test_build_skips_written(ctx, fake, run):
    name = _analysis(ctx)
    pid = _update(ctx, name, "@I3@", "2 JAN 1878")
    api.build_iteration(ctx, name)
    _apply(ctx, name, fake, run)
    result = api.build_iteration(ctx, name)
    assert pid not in result.applied and pid not in result.stale
    assert store.load(ctx.kb, name).last_build.proposals == []


def test_written_but_missing(ctx, fake, run, repo):
    name = _analysis(ctx)
    pid = _update(ctx, name, "@I3@", "2 JAN 1878")
    api.build_iteration(ctx, name)
    _apply(ctx, name, fake, run)
    analysis = store.load(ctx.kb, name)
    p = next(x for x in analysis.proposals if x.id == pid)
    assert written_but_missing(ctx, analysis, p)
    assert api.read_analysis(ctx, name).not_in_tree == [pid]
    report = (store.analysis_dir(ctx.kb, name) / "report.md").read_text(encoding="utf-8")
    assert f"**{pid}** (user, written, not in the current tree)" in report
    (repo / "tree.ged").write_bytes(TREE.replace("2 DATE 1 JAN 1878", "2 DATE 2 JAN 1878").encode("utf-8"))
    index_kb(ctx.kb, FakeEmbedder())
    assert not written_but_missing(ctx, analysis, p)


def test_snapshot_only(ctx, fake, run, prs, repo):
    assert snapshot_only(ctx, GED, fake.transport, run).endswith(PR_URL)
    assert prs[-1][prs[-1].index("--title") + 1].endswith(" from Gramps Web")
    assert "no genea writes" in _body(prs)
    assert git(repo, "log", "--format=%s", "-1", f"genea/{snapshot._today()}-snapshot").strip().endswith(
        "from Gramps Web")
    git(repo, "merge", f"genea/{snapshot._today()}-snapshot")
    git(repo, "push", "origin", "main")
    prs.clear()
    assert "matches main" in snapshot_only(ctx, GED, fake.transport, run)
    assert prs == []


def test_crash_before_anything_written_is_raised_as_is(ctx, fake, run, monkeypatch):
    name = _analysis(ctx)
    _update(ctx, name, "@I3@", "2 JAN 1878")
    api.build_iteration(ctx, name)

    def handler(request):
        if request.method == "PUT":
            raise RuntimeError("crash")
        return fake._handle(request)

    def crashing(client, target, log):
        raise RuntimeError("crash")

    monkeypatch.setattr("genea.analysis.apply.write_proposal", crashing)
    with pytest.raises(RuntimeError):
        _apply(ctx, name, fake, run)


def test_failed_proposal_is_retried_after_rebuild(ctx, fake, run):
    name = _analysis(ctx)
    pid = _update(ctx, name, "@I3@", "2 JAN 1878")
    api.build_iteration(ctx, name)
    fake.fail_on[("PUT", "/api/events/")] = 500
    result = _apply(ctx, name, fake, run)
    assert pid in result.failed and _proposal(ctx, name, pid).status == "failed"
    built = api.build_iteration(ctx, name)
    assert pid in built.applied
    p = _proposal(ctx, name, pid)
    assert p.status == "active" and p.error is None
    assert store.load(ctx.kb, name).last_build.proposals == [pid]
    assert _apply(ctx, name, fake, run).written == [pid]
    assert _proposal(ctx, name, pid).status == "written"


def test_conflict_after_a_restored_update_is_stale(ctx, fake, run):
    name = _analysis(ctx)
    link = api.propose(ctx, name, "link_child", {"family_id": f"{GED}#@F1@", "child_id": f"{GED}#@I7@"},
                       CITE, "why").proposal_id
    api.build_iteration(ctx, name)
    fake.fail_on[("PUT", "/api/people/")] = 412  # the child's PUT, after the family PUT went through
    before = list(fake.objects["families"]["hF1"]["child_ref_list"])
    result = _apply(ctx, name, fake, run)
    assert result.written == [] and link in result.stale and result.failed == {}
    assert _proposal(ctx, name, link).status == "stale"
    assert fake.objects["families"]["hF1"]["child_ref_list"] == before


def test_pure_conflict_is_stale(ctx, fake, run):
    name = _analysis(ctx)
    pid = _update(ctx, name, "@I3@", "2 JAN 1878")
    api.build_iteration(ctx, name)
    fake.fail_on[("PUT", "/api/events/")] = 412
    result = _apply(ctx, name, fake, run)
    assert "changed in Gramps while writing" in result.stale[pid]


def test_failed_new_person_makes_its_link_stale(ctx, fake, run):
    name = _analysis(ctx)
    person, link = _new_child(ctx, name)
    api.build_iteration(ctx, name)
    fake.fail_on[("POST", "/api/people/")] = 500
    result = _apply(ctx, name, fake, run)
    assert person in result.failed
    assert result.stale[link] == f"{person} was not written"


def test_partial_undo_says_how_many(ctx, fake, run):
    name = _analysis(ctx)
    person, _ = _new_child(ctx, name)
    api.withdraw(ctx, name, "p-2")
    api.build_iteration(ctx, name)
    _apply(ctx, name, fake, run)
    p = _proposal(ctx, name, person)
    created = next(o.handle for o in p.written.objects if o.kind == "people")
    newest = max(o.transaction for o in p.written.objects)
    oldest = min(o.transaction for o in p.written.objects)
    count = len({o.transaction for o in p.written.objects})
    real = fake._undo

    def undo(tid):  # the oldest transaction (the person's creation) no longer applies
        if tid == oldest:
            return fake._json(409, {"error": {"message": "Object changed"}})
        return real(tid)

    fake._undo = undo
    result = undo_proposal(ctx, name, person, fake.transport, run)
    assert f"{count - 1} of {count} transaction(s) were undone in Gramps, the rest stay" in result.failed[person]
    assert "fix person" in result.failed[person]
    assert _proposal(ctx, name, person).status == "written"
    assert [o.transaction for o in _proposal(ctx, name, person).written.objects] == [oldest]
    assert created in fake.objects["people"] and newest != oldest
