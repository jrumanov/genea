import pytest

from genea.analysis.scope import resolve_scope
from genea.tools.base import ToolError


def test_person_scope(actx):
    spec, scope = resolve_scope(actx, "person", "tree.ged#@I3@")
    assert spec.id == "tree.ged#@I3@" and spec.kind == "person"
    assert scope.ged_file == "tree.ged"
    assert scope.people == ["@I3@"] and scope.families == ["@F1@"] and scope.boundary == []


def test_person_scope_accepts_a_bare_xref(actx):
    spec, _ = resolve_scope(actx, "person", "I3")
    assert spec.id == "tree.ged#@I3@"


def test_family_scope(actx):
    spec, scope = resolve_scope(actx, "family", "tree.ged#@F1@")
    assert spec.id == "tree.ged#@F1@"
    assert scope.people == ["@I1@", "@I2@", "@I3@", "@I4@", "@I5@"]
    assert set(scope.families) == {"@F1@", "@F0@"}


def test_branch_ancestors_stops_at_the_depth(actx):
    _, scope = resolve_scope(actx, "branch", "tree.ged#@I3@", "ancestors", 1)
    assert scope.people == ["@I3@", "@I1@", "@I2@"]
    assert scope.boundary == ["@I2@"]  # her parents are beyond depth 1
    _, scope = resolve_scope(actx, "branch", "tree.ged#@I3@", "ancestors", 2)
    assert scope.people == ["@I3@", "@I1@", "@I2@", "@I6@"] and scope.boundary == []


def test_branch_descendants_and_both(actx):
    _, scope = resolve_scope(actx, "branch", "tree.ged#@I6@", "descendants", 1)
    assert scope.people == ["@I6@", "@I2@"] and scope.boundary == ["@I2@"]
    assert set(scope.families) == {"@F0@", "@F1@"}
    _, scope = resolve_scope(actx, "branch", "tree.ged#@I2@", "both", 1)
    assert set(scope.people) == {"@I2@", "@I6@", "@I3@", "@I4@", "@I5@"}


def test_a_loop_in_the_tree_ends(tmp_path):
    from fixtures.analysis_tree import TREE, make_analysis_kb

    from genea.index.embed import FakeEmbedder
    from genea.tools.base import open_tools

    looped = TREE.replace("0 @F0@ FAM\n1 HUSB @I6@\n", "0 @F0@ FAM\n1 HUSB @I3@\n")  # I2's father is her son
    ctx = open_tools(make_analysis_kb(tmp_path / "loop", looped), FakeEmbedder())
    _, scope = resolve_scope(ctx, "branch", "tree.ged#@I3@", "both", 6)
    ctx.conn.close()
    assert len(scope.people) == len(set(scope.people))
    # ancestors: I1, I2 (her father I3 is the root); descendants via F0: I2, then her children I4, I5
    assert set(scope.people) == {"@I3@", "@I1@", "@I2@", "@I4@", "@I5@"}


def test_depth_is_clamped(actx):
    spec, _ = resolve_scope(actx, "branch", "tree.ged#@I3@", "ancestors", 99)
    assert spec.depth == 6


@pytest.mark.parametrize(("kind", "target", "direction", "match"), [
    ("clan", "tree.ged#@I3@", "both", "person, family or branch"),
    ("branch", "tree.ged#@I3@", "sideways", "ancestors, descendants or both"),
    ("person", "tree.ged#@I99@", "both", "No person"),
    ("family", "tree.ged#@F99@", "both", "No family"),
])
def test_bad_scopes(actx, kind, target, direction, match):
    with pytest.raises(ToolError, match=match):
        resolve_scope(actx, kind, target, direction)
