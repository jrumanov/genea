import pytest
from fixtures.analysis_tree import TREE, make_analysis_kb

from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.kb import init_kb
from genea.tools.base import open_tools
from genea.tools.conflicts import find_conflicts

T = "tree.ged#"

SYNTHETIC = """0 HEAD
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Otec /Mladý/
1 SEX M
1 BIRT
2 DATE 1900
1 FAMS @F1@
0 @I2@ INDI
1 NAME Matka /Stará/
1 SEX F
1 BIRT
2 DATE 1800
1 FAMS @F1@
0 @I3@ INDI
1 NAME Dítě /Mladé/
1 BIRT
2 DATE 1905
1 BAPM
2 DATE 1890
1 FAMC @F1@
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 MARR
2 DATE 1908
1 CHIL @I3@
0 TRLR
"""


@pytest.fixture
def synthetic(tmp_path):
    root = tmp_path / "kb"
    root.mkdir()
    (root / "tree.ged").write_text(SYNTHETIC, encoding="utf-8")
    kb = init_kb(root)
    index_kb(kb, FakeEmbedder())
    ctx = open_tools(kb, FakeEmbedder())
    yield ctx
    ctx.conn.close()


def test_rule_checks(synthetic):
    found = {(c.kind, c.person_id) for c in find_conflicts(synthetic).conflicts}
    assert found == {
        ("parent_too_young", T + "@I3@"),
        ("mother_too_old", T + "@I3@"),
        ("baptism_before_birth", T + "@I3@"),
        ("married_too_young", T + "@I1@"),
    }


def test_sample_has_exactly_one_hard_error(tools):
    errors = [c for c in find_conflicts(tools).conflicts if c.severity == "error"]
    assert [(c.kind, c.person_id) for c in errors] == [("event_after_death", T + "@I4@")]
    assert "1855" in errors[0].message
    assert all(s.file == "tree.ged" and s.line for s in errors[0].sources)


def test_document_mismatch_needs_review(tools):
    [conflict] = [c for c in find_conflicts(tools, T + "@I6@").conflicts if c.kind == "document_mismatch"]
    assert conflict.severity == "needs_review"
    assert conflict.sources[0].file == "docs/poznamky.md"
    assert "1851" in conflict.message


def test_limit(tools):
    assert len(find_conflicts(tools, limit=1).conflicts) == 1


def test_birth_after_mother_death_and_close_siblings(actx):
    found = {(c.kind, c.person_id, c.severity) for c in find_conflicts(actx).conflicts}
    assert ("born_after_mother_death", "tree.ged#@I5@", "error") in found
    assert ("siblings_too_close", "tree.ged#@I4@", "needs_review") in found
    assert not any(k == "siblings_too_close" and p == "tree.ged#@I3@" for k, p, _ in found)


def test_twins_and_baptisms_do_not_fire(tmp_path):
    tree = (TREE.replace("2 DATE 1 JUN 1878", "2 DATE 3 JAN 1878")
            .replace("1 BIRT\n2 DATE 2 FEB 1881", "1 BAPM\n2 DATE 2 FEB 1881"))
    kb = make_analysis_kb(tmp_path / "t", tree)
    ctx = open_tools(kb, FakeEmbedder())
    kinds = {c.kind for c in find_conflicts(ctx).conflicts}
    ctx.conn.close()
    assert "siblings_too_close" not in kinds and "born_after_mother_death" not in kinds
