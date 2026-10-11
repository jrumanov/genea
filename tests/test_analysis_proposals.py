import pytest
from fixtures.analysis_tree import event_line
from typer.testing import CliRunner

from genea.analysis import api, store
from genea.cli.main import app
from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.tools.base import ToolError, open_tools

CITE = ["[docs/zapis.txt]"]
runner = CliRunner()


@pytest.fixture
def name(actx):
    return api.analyze(actx, "family", "tree.ged#@F1@").name


def test_update_event_records_an_anchor(actx, analysis_kb, name):
    before = (analysis_kb.root / "tree.ged").read_bytes()
    line = event_line(actx, "@I3@", "BIRT")
    result = api.propose(actx, name, "update_event", {"person_id": "@I3@", "line": str(line), "date": "2 JAN 1878"},
                         CITE, "the register says 2 January")
    assert result.proposal_id == "p-1" and result.status == "active"
    [p] = store.load(analysis_kb, name).proposals
    assert p.anchor == {"type": "BIRT", "description": None, "date": "1 JAN 1878", "place": "Brno"}
    assert p.base_hash == store.load(analysis_kb, name).base_hash
    assert p.payload == {"person_id": "tree.ged#@I3@", "line": line, "date": "2 JAN 1878"}
    assert p.author == "user" and p.sources[0].file == "docs/zapis.txt"
    assert (analysis_kb.root / "tree.ged").read_bytes() == before
    assert "p-1" in (analysis_kb.analyses_dir / name / "report.md").read_text(encoding="utf-8")


def test_identical_proposal_is_not_repeated(actx, name):
    payload = {"person_id": "tree.ged#@I3@", "text": "Mlynár"}
    first = api.propose(actx, name, "add_note", payload, CITE, "x")
    again = api.propose(actx, name, "add_note", payload, CITE, "x")
    assert again.proposal_id == first.proposal_id and "already exists" in again.message


@pytest.mark.parametrize(("operation", "payload", "sources", "rationale", "match"), [
    ("add_note", {"person_id": "tree.ged#@I3@", "text": "x"}, [], "why", "Cite at least one source"),
    ("add_note", {"person_id": "tree.ged#@I3@", "text": "x"}, ["[docs/none.pdf p.1]"], "why", "not an indexed file"),
    ("add_note", {"person_id": "tree.ged#@I3@", "text": "x"}, CITE, "  ", "rationale"),
    ("rename", {"person_id": "tree.ged#@I3@"}, CITE, "why", "Unknown operation"),
    ("add_event", {"person_id": "tree.ged#@I3@", "type": "PARTY"}, CITE, "why", "not an individual event"),
    ("add_event", {"person_id": "tree.ged#@I99@", "type": "BAPM"}, CITE, "why", "No person"),
    ("update_event", {"person_id": "tree.ged#@I3@", "line": 1, "date": "1878"}, CITE, "why", "is not an event of"),
    ("update_event", {"person_id": "tree.ged#@I3@", "line": "x", "date": "1878"}, CITE, "why", "line must be"),
    ("link_child", {"family_id": "tree.ged#@F1@", "child_id": "proposal:p-9"}, CITE, "why", "not an active add_person"),
])
def test_bad_proposals_are_refused(actx, name, operation, payload, sources, rationale, match):
    with pytest.raises(ToolError, match=match):
        api.propose(actx, name, operation, payload, sources, rationale)


@pytest.mark.parametrize(("operation", "payload", "match"), [
    ("add_event", {"person_id": "tree.ged#@I3@", "type": "BAPM", "date": "1878\n0 @X@ INDI"}, "one line"),
    ("add_event", {"person_id": "tree.ged#@I3@", "type": "BAPM", "place": 12}, "must be text"),
    ("add_event", {"person_id": "tree.ged#@I3@", "type": ["BAPM"]}, "must be text"),
    ("add_event", {"person_id": "tree.ged#@I3@", "type": {"x": 1}}, "must be text"),
    ("add_event", {"person_id": "tree.ged#@I3@", "type": "BAPM", "place": "x" * 300}, "characters"),
    ("add_person", {"given": "Mária\r", "surname": "Weiss"}, "one line"),
    ("add_person", {"given": "Mária", "surname": "Weiss", "birth_date": 1883}, "must be text"),
    ("add_person", {"given": "Mária", "surname": "Weiss", "birth_place": "Brno\n1 SEX M"}, "one line"),
    ("add_source_citation", {"person_id": "tree.ged#@I3@", "source": "Matrika", "page": "f. 1\n2 X"}, "one line"),
    ("add_note", {"person_id": ["tree.ged#@I3@"], "text": "x"}, "must be text"),
    ("update_event", {"person_id": "tree.ged#@I3@", "line": 40, "place": "Brno\nx"}, "one line"),
])
def test_bad_payload_values_are_refused(actx, name, operation, payload, match):
    with pytest.raises(ToolError, match=match):
        api.propose(actx, name, operation, payload, CITE, "why")


def test_multiline_note_and_source_are_allowed(actx, analysis_kb, name):
    api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I3@", "text": "a\nb"}, CITE, "why")
    api.propose(actx, name, "add_source_citation", {"person_id": "tree.ged#@I3@", "source": "Matrika\nBrno"},
                CITE, "why")
    assert len(store.load(analysis_kb, name).proposals) == 2


def test_identical_stale_proposal_is_not_repeated(actx, analysis_kb, name):
    payload = {"person_id": "tree.ged#@I3@", "text": "Mlynár"}
    first = api.propose(actx, name, "add_note", payload, CITE, "x")
    saved = store.load(analysis_kb, name)
    saved.proposals[0].status = "stale"
    store.save(analysis_kb, saved)
    again = api.propose(actx, name, "add_note", payload, CITE, "x")
    assert again.proposal_id == first.proposal_id and "already exists" in again.message


def test_analysis_made_on_an_outdated_index_needs_a_new_analyze(actx, analysis_kb):
    path = analysis_kb.root / "tree.ged"
    path.write_text(path.read_text(encoding="utf-8").replace("0 TRLR", "0 @N9@ NOTE x\n0 TRLR"), encoding="utf-8")
    name = api.analyze(actx, "person", "tree.ged#@I3@").name
    assert api.read_analysis(actx, name).index_state == "outdated"
    index_kb(analysis_kb, FakeEmbedder())
    ctx = open_tools(analysis_kb, FakeEmbedder())
    try:
        assert api.read_analysis(ctx, name).index_state == "outdated"  # analysed before the reindex
        with pytest.raises(ToolError, match="analyze again"):
            api.propose(ctx, name, "add_note", {"person_id": "tree.ged#@I3@", "text": "x"}, CITE, "why")
        assert api.read_analysis(ctx, api.analyze(ctx, "person", "tree.ged#@I3@").name).index_state == "ready"
    finally:
        ctx.conn.close()


def test_read_analysis_reports_a_changed_tree(actx, analysis_kb, name):
    assert api.read_analysis(actx, name).index_state == "ready"
    path = analysis_kb.root / "tree.ged"
    path.write_text(path.read_text(encoding="utf-8").replace("0 TRLR", "0 @N9@ NOTE x\n0 TRLR"), encoding="utf-8")
    page = api.read_analysis(actx, name)
    assert page.index_state == "outdated" and "genea index" in page.message


def test_update_event_needs_a_new_value(actx, name):
    line = event_line(actx, "@I3@", "BIRT")
    with pytest.raises(ToolError, match="new date or place"):
        api.propose(actx, name, "update_event", {"person_id": "tree.ged#@I3@", "line": line}, CITE, "why")


def test_unknown_finding(actx, name):
    with pytest.raises(ToolError, match="No finding f-00000000"):
        api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I3@", "text": "x"}, CITE, "why",
                    finding_id="f-00000000")


def test_refused_when_the_tree_changed_after_indexing(actx, analysis_kb, name):
    path = analysis_kb.root / "tree.ged"
    path.write_text(path.read_text(encoding="utf-8").replace("0 TRLR", "0 @N9@ NOTE x\n0 TRLR"), encoding="utf-8")
    with pytest.raises(ToolError, match="genea index"):
        api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I3@", "text": "x"}, CITE, "why")


def test_new_person_and_withdraw_order(actx, analysis_kb, name):
    person = api.propose(actx, name, "add_person", {"given": "Mária", "surname": "Weiss", "sex": "F"}, CITE, "why")
    link = api.propose(actx, name, "link_child",
                       {"family_id": "F1", "child_id": f"proposal:{person.proposal_id}"}, CITE, "why")
    assert store.load(analysis_kb, name).proposals[1].payload["family_id"] == "tree.ged#@F1@"
    with pytest.raises(ToolError, match=f"withdraw {link.proposal_id} first"):
        api.withdraw(actx, name, person.proposal_id)
    assert api.withdraw(actx, name, link.proposal_id).status == "withdrawn"
    assert api.withdraw(actx, name, person.proposal_id).status == "withdrawn"
    assert store.load(analysis_kb, name).counts()["proposals"] == 0
    with pytest.raises(ToolError, match="No proposal p-9"):
        api.withdraw(actx, name, "p-9")


def test_dismiss_and_notes_survive_a_rerun(actx, analysis_kb, name):
    finding = api.read_analysis(actx, name).findings[0]
    api.dismiss(actx, name, finding.id, "known from the register")
    api.add_note(actx, name, "Checked the Brno register.")
    api.analyze(actx, "family", "tree.ged#@F1@")
    saved = store.load(analysis_kb, name)
    assert saved.dismissed == {finding.id: "known from the register"}
    assert saved.notes[0].text == "Checked the Brno register."
    report = (analysis_kb.analyses_dir / name / "report.md").read_text(encoding="utf-8")
    assert "dismissed (known from the register)" in report and "## Notes" in report
    with pytest.raises(ToolError, match="No finding"):
        api.dismiss(actx, name, "f-00000000")
    with pytest.raises(ToolError, match="empty"):
        api.add_note(actx, name, " ")


def test_cli_propose_withdraw_dismiss(analysis_kb, monkeypatch, actx, name):
    monkeypatch.setattr("genea.cli.common.get_embedder", lambda kb: FakeEmbedder())
    root = str(analysis_kb.root)
    result = runner.invoke(app, ["--kb", root, "analysis", "propose", name, "--operation", "add_note",
                                 "--payload", '{"person_id": "tree.ged#@I3@", "text": "Mlynár"}',
                                 "--source", "[docs/zapis.txt]", "--rationale", "the register"])
    assert result.exit_code == 0, result.output
    assert "p-1" in result.output
    assert store.load(analysis_kb, name).proposals[0].author == "user"
    result = runner.invoke(app, ["--kb", root, "analysis", "propose", name, "--operation", "add_note",
                                 "--payload", "[1]", "--source", "[docs/zapis.txt]", "--rationale", "x"])
    assert result.exit_code == 1 and "JSON object" in result.output
    assert runner.invoke(app, ["--kb", root, "analysis", "withdraw", name, "p-1"]).exit_code == 0
    finding = store.load(analysis_kb, name).findings[0].id
    result = runner.invoke(app, ["--kb", root, "analysis", "dismiss", name, finding, "--reason", "known"])
    assert result.exit_code == 0 and store.load(analysis_kb, name).dismissed == {finding: "known"}
