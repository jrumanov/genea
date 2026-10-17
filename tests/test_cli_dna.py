import pytest
from fixtures.dna import write_csv
from typer.testing import CliRunner

from genea.cli.main import app
from genea.dna import api, runtime
from genea.dna.store import DnaError, HaploCall, Haplogroups

runner = CliRunner()
pytest.importorskip("snps")


def flat(result) -> str:
    """Rich wraps at 80 columns; compare the text with the line breaks removed."""
    return " ".join(result.output.split())


def test_import_status_and_analyze(analysis_kb, tmp_path, monkeypatch):
    kb = ["--kb", str(analysis_kb.root)]
    csv = write_csv(tmp_path / "MyHeritage_raw_dna_data.csv")
    result = runner.invoke(app, [*kb, "dna", "import", str(csv), "--person", "tree.ged#@I1@"])
    assert result.exit_code == 0, result.output
    assert "Person: Karol Weiss (tree.ged#@I1@)" in flat(result) and "DNA name: tree.ged-I1" in flat(result)

    again = runner.invoke(app, [*kb, "dna", "import", str(csv), "--person", "tree.ged#@I1@"])
    assert again.exit_code == 1 and "--replace" in flat(again)

    status = runner.invoke(app, [*kb, "dna", "status"])
    assert status.exit_code == 0 and "not analysed yet: genea dna analyze tree.ged-I1" in flat(status)

    monkeypatch.setattr(api, "analyze", lambda kb_, person: Haplogroups(analyzed_at="x", calls=[
        HaploCall(line="mtDNA", haplogroup="H1", quality=0.9, markers_used=300),
        HaploCall(line="Y", haplogroup="R-P312", detail="R1b1a2a1a2", markers_used=1500)]))
    done = runner.invoke(app, [*kb, "dna", "analyze", "tree.ged-I1"])
    assert done.exit_code == 0, done.output
    assert "mtDNA: H1 from 300 markers" in flat(done) and "Y: R-P312 (R1b1a2a1a2)" in flat(done)
    assert "None" not in done.output


def test_analyze_exits_1_when_a_tool_is_missing(analysis_kb, monkeypatch):
    monkeypatch.setattr(api, "analyze", lambda kb_, person: Haplogroups(analyzed_at="x", calls=[
        HaploCall(line="mtDNA", skipped_reason="no Java 11+ for Haplogrep; run `genea dna setup`."),
        HaploCall(line="Y", haplogroup="R-P312", markers_used=10)]))
    result = runner.invoke(app, ["--kb", str(analysis_kb.root), "dna", "analyze", "x"])
    assert result.exit_code == 1 and "genea dna setup" in flat(result)


def test_analyze_unknown_person(analysis_kb):
    result = runner.invoke(app, ["--kb", str(analysis_kb.root), "dna", "analyze", "nobody"])
    assert result.exit_code == 1 and "No DNA for 'nobody'" in flat(result)


def test_setup_reports_messages_and_errors(monkeypatch):
    monkeypatch.setattr(runtime, "setup", lambda: ["Java: system Java 21", "yhaplo 2.1.19"])
    ok = runner.invoke(app, ["dna", "setup"])
    assert ok.exit_code == 0 and "yhaplo 2.1.19" in flat(ok)

    def fail():
        raise DnaError("Checksum mismatch for x")
    monkeypatch.setattr(runtime, "setup", fail)
    bad = runner.invoke(app, ["dna", "setup"])
    assert bad.exit_code == 1 and "Checksum mismatch" in flat(bad)
