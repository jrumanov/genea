import pytest
from typer.testing import CliRunner

from genea.analysis import api, store
from genea.cli.main import app
from genea.index.embed import FakeEmbedder
from genea.tools.base import ToolError

runner = CliRunner()


def test_analyze_writes_the_analysis_and_report(actx, analysis_kb):
    before = (analysis_kb.root / "tree.ged").read_bytes()
    page = api.analyze(actx, "family", "tree.ged#@F1@")
    assert page.name == "tree-family-F1" and page.index_state == "ready" and page.message is None
    assert page.counts["error"] == 1 and page.counts["needs_review"] == 1
    assert page.findings[0].kind == "born_after_mother_death" and page.findings[0].status == "open"
    folder = analysis_kb.analyses_dir / "tree-family-F1"
    report = (folder / "report.md").read_text(encoding="utf-8")
    assert "## Needs a decision" in report and page.findings[0].id in report
    assert "### Anna Weiss (tree.ged#@I5@)" in report
    assert page.report == ".genea/analyses/tree-family-F1/report.md"
    assert (analysis_kb.root / "tree.ged").read_bytes() == before


def test_rerun_keeps_dismissals_notes_and_proposals(actx, analysis_kb):
    first = api.analyze(actx, "person", "tree.ged#@I5@")
    saved = store.load(analysis_kb, first.name)
    saved.dismissed[first.findings[0].id] = "known"
    saved.iterations = 2
    store.save(analysis_kb, saved)
    again = api.analyze(actx, "person", "tree.ged#@I5@")
    kept = store.load(analysis_kb, again.name)
    assert kept.dismissed == {first.findings[0].id: "known"} and kept.iterations == 2
    assert again.findings[0].status == "dismissed"


def test_paging(actx, monkeypatch):
    monkeypatch.setattr(api, "PAGE_SIZE", 2)
    first = api.analyze(actx, "family", "tree.ged#@F1@")
    assert first.pages > 1 and len(first.findings) == 2
    second = api.read_analysis(actx, first.name, page=2)
    assert {f.id for f in second.findings}.isdisjoint({f.id for f in first.findings})
    only_info = api.read_analysis(actx, first.name, severity="info")
    assert all(f.severity == "info" for f in only_info.findings)
    with pytest.raises(ToolError, match="page must be between 1 and"):
        api.read_analysis(actx, first.name, page=99)
    with pytest.raises(ToolError, match="severity"):
        api.read_analysis(actx, first.name, severity="fatal")


def test_tree_changed_after_indexing_is_reported(actx, analysis_kb):
    path = analysis_kb.root / "tree.ged"
    path.write_text(path.read_text(encoding="utf-8").replace("0 TRLR", "0 @N9@ NOTE x\n0 TRLR"), encoding="utf-8")
    page = api.analyze(actx, "person", "tree.ged#@I3@")
    assert page.index_state == "outdated" and "genea index" in page.message
    assert "> " in (analysis_kb.analyses_dir / page.name / "report.md").read_text(encoding="utf-8")


def test_unknown_analysis(actx):
    with pytest.raises(ToolError, match="No analysis named"):
        api.read_analysis(actx, "tree-person-I99")


def test_list_all(actx):
    api.analyze(actx, "person", "tree.ged#@I3@")
    api.analyze(actx, "person", "tree.ged#@I5@")
    assert [a.name for a in api.list_all(actx).analyses] == ["tree-person-I3", "tree-person-I5"]


def test_cli_analyze_list_and_show(analysis_kb, monkeypatch):
    monkeypatch.setattr("genea.cli.common.get_embedder", lambda kb: FakeEmbedder())
    root = str(analysis_kb.root)
    result = runner.invoke(app, ["--kb", root, "analyze", "person", "tree.ged#@I5@"])
    assert result.exit_code == 0, result.output
    assert "tree-person-I5" in result.output and "report:" in result.output
    assert (analysis_kb.analyses_dir / "tree-person-I5" / "analysis.json").is_file()
    result = runner.invoke(app, ["--kb", root, "analysis", "list"])
    assert result.exit_code == 0 and "tree-person-I5" in result.output
    result = runner.invoke(app, ["--kb", root, "analysis", "show", "tree-person-I5", "--severity", "error"])
    assert result.exit_code == 0 and "error" in result.output
    result = runner.invoke(app, ["--kb", root, "analyze", "clan", "tree.ged#@I5@"])
    assert result.exit_code == 1 and "person, family or branch" in result.output
