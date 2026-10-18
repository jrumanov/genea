import json

import pytest
from fixtures.dna import add_person

from genea.dna import store, tools
from genea.dna.limits import RsidBudget
from genea.dna.store import HaploCall, Haplogroups
from genea.kb import init_kb
from genea.tool_runner import run_kb_tool
from genea.tools.base import ToolError


@pytest.fixture
def kb(tmp_path):
    kb = init_kb(tmp_path / "kb")
    add_person(kb)
    return kb


def test_summary_before_analysis_names_the_command(kb):
    out = json.loads(run_kb_tool(kb, tools.dna_summary, "tree.ged#@I12@").text)
    assert out["person"] == "tree.ged-I12" and out["sex"] == "male"
    assert "genea dna analyze tree.ged-I12" in out["haplogroups"]
    assert out["sources"] == [{"file": "dna/tree.ged-I12/meta.json"}]
    assert "MyHeritage_raw_dna_data.csv" not in json.dumps(out)


def test_haplogroups_with_caveat_and_citation(kb):
    with pytest.raises(ToolError, match="genea dna analyze"):
        tools.haplogroups(kb, "tree.ged-I12")
    store.save_haplogroups(kb, "tree.ged-I12", Haplogroups(analyzed_at="2026-10-15T11:00:00", calls=[
        HaploCall(line="Y", haplogroup="R-P312", detail="R1b1a2a1a2", markers_used=16, tool="yhaplo")]))
    result = tools.haplogroups(kb, "tree.ged-I12")
    assert result.calls[0].haplogroup == "R-P312" and "paternal" in result.caveat
    assert result.sources[0].cite() == "[dna/tree.ged-I12/haplogroups.json]"


def test_snp_lookup_answers_found_missing_and_no_call(kb):
    budget = RsidBudget(200)
    result = tools.snp_lookup(kb, budget, "tree.ged-I12", ["RS8000", "rs404", "rs1003"])
    by_id = {r.rsid: r for r in result.results}
    assert by_id["rs8000"].genotype == "TT" and by_id["rs8000"].chrom == "Y"
    assert "not on this chip" in by_id["rs404"].note
    assert result.lookups_left == 197
    assert result.sources[0].cite() == "[dna/tree.ged-I12/genotypes.sqlite]"


def test_rsids_are_normalised_and_charged_once():
    assert tools.normalize_rsids(["rs1, RS2;rs1", "rs2"]) == ["rs1", "rs2"]
    assert tools.normalize_rsids("i3000002") == ["i3000002"]


def test_duplicates_and_mixed_case_are_charged_once_per_distinct_rsid(kb):
    budget = RsidBudget(200)
    result = tools.snp_lookup(kb, budget, "tree.ged-I12", "rs8000, RS8000;Rs8000 rs8001")
    assert [r.rsid for r in result.results] == ["rs8000", "rs8001"]
    assert budget.left == 198 and result.lookups_left == 198


def test_25_duplicates_of_one_rsid_are_not_over_the_cap(kb):
    budget = RsidBudget(200)
    tools.snp_lookup(kb, budget, "tree.ged-I12", ["rs8000"] * 25)
    assert budget.left == 199


def test_invalid_rsids_cost_nothing(kb):
    budget = RsidBudget(200)
    with pytest.raises(ToolError, match="Not rsIDs: 4988235"):
        tools.snp_lookup(kb, budget, "tree.ged-I12", ["rs1", "4988235"])
    with pytest.raises(ToolError, match="1-20"):
        tools.snp_lookup(kb, budget, "tree.ged-I12", [])
    assert budget.left == 200


def test_more_than_20_is_rejected_not_truncated(kb):
    budget = RsidBudget(200)
    with pytest.raises(ToolError, match=r"At most 20 rsIDs per call \(21 given\)"):
        tools.snp_lookup(kb, budget, "tree.ged-I12", [f"rs{i}" for i in range(21)])
    assert budget.left == 200


def test_session_budget_stops_paging(kb):
    budget = RsidBudget(30)
    tools.snp_lookup(kb, budget, "tree.ged-I12", [f"rs{1000 + i}" for i in range(20)])
    with pytest.raises(ToolError, match="Privacy limit.*30.*10 are left.*dna_session_rsids"):
        tools.snp_lookup(kb, budget, "tree.ged-I12", [f"rs{1100 + i}" for i in range(20)])
    tools.snp_lookup(kb, budget, "tree.ged-I12", [f"rs{1100 + i}" for i in range(10)])
    assert budget.left == 0


def test_run_kb_tool_turns_errors_into_text(kb):
    out = run_kb_tool(kb, tools.dna_summary, "nobody")
    assert out.is_error and "No DNA for 'nobody'" in json.loads(out.text)["error"]
