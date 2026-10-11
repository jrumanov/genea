import pytest

from genea.analysis import store
from genea.analysis.models import Analysis, ScopeSpec, finding_id, parse_cite
from genea.kb import init_kb
from genea.tools.base import ToolError
from genea.tools.models import SourceRef


def _analysis(name="tree-person-I1"):
    return Analysis(name=name, scope=ScopeSpec(kind="person", id="tree.ged#@I1@"), ged_file="tree.ged",
                    base_hash="ab" * 32, index_state="ready", created="2026-10-11T10:00:00",
                    updated="2026-10-11T10:00:00")


def test_init_creates_the_analyses_folder(tmp_path):
    kb = init_kb(tmp_path)
    assert kb.analyses_dir == tmp_path.resolve() / ".genea" / "analyses"
    assert kb.analyses_dir.is_dir()


def test_save_and_load_round_trip(tmp_path):
    kb = init_kb(tmp_path)
    analysis = _analysis()
    analysis.dismissed["f-1"] = "known"
    path = store.save(kb, analysis)
    assert path == kb.analyses_dir / "tree-person-I1" / "analysis.json"
    assert store.load(kb, "tree-person-I1") == analysis
    assert [a.name for a in store.list_analyses(kb)] == ["tree-person-I1"]


@pytest.mark.parametrize("name", ["", ".", "..", "...", "../x", "a/b", "a\\b", "x y", "C:x"])
def test_bad_names_are_refused(tmp_path, name):
    kb = init_kb(tmp_path)
    with pytest.raises(ToolError, match="Invalid analysis name"):
        store.analysis_dir(kb, name)


def test_missing_and_damaged_analyses(tmp_path):
    kb = init_kb(tmp_path)
    with pytest.raises(ToolError, match="No analysis named"):
        store.load(kb, "nothing")
    folder = kb.analyses_dir / "broken"
    folder.mkdir()
    (folder / "analysis.json").write_text("{", encoding="utf-8")
    with pytest.raises(ToolError, match="damaged"):
        store.load(kb, "broken")
    assert store.list_analyses(kb) == []


def test_scope_names():
    assert store.scope_name("tree.ged", ScopeSpec(kind="person", id="tree.ged#@I12@")) == "tree-person-I12"
    assert store.scope_name("sub dir/my tree.ged", ScopeSpec(kind="family", id="sub dir/my tree.ged#@F3@")) \
        == "my_tree-family-F3"
    spec = ScopeSpec(kind="branch", id="tree.ged#@I1@", direction="ancestors", depth=3)
    assert store.scope_name("tree.ged", spec) == "tree-branch-I1-ancestors-3"


def test_finding_ids_are_stable_and_distinct():
    a = finding_id("missing_birth", ["tree.ged#@I1@"], None, "")
    assert a == finding_id("missing_birth", ["tree.ged#@I1@"], None, "")
    assert a != finding_id("missing_birth", ["tree.ged#@I2@"], None, "")
    assert a.startswith("f-") and len(a) == 10


@pytest.mark.parametrize(("text", "ref"), [
    ("[tree.ged:L1042]", SourceRef(file="tree.ged", line=1042)),
    ("[docs/matrika.pdf p.3]", SourceRef(file="docs/matrika.pdf", page=3)),
    ("[recordings/x y.txt @14:20]", SourceRef(file="recordings/x y.txt", start_s=860.0)),
    ("[recordings/x.txt @1:02:03]", SourceRef(file="recordings/x.txt", start_s=3723.0)),
    (" [photos/x.jpg] ", SourceRef(file="photos/x.jpg")),
])
def test_parse_cite(text, ref):
    assert parse_cite(text) == ref


@pytest.mark.parametrize("text", ["tree.ged:L1", "[]", "[a] b", ""])
def test_parse_cite_rejects(text):
    with pytest.raises(ToolError, match="Not a citation"):
        parse_cite(text)
