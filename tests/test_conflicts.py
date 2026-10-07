import pytest

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
