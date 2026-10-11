from fixtures.analysis_tree import TREE, make_analysis_kb

from genea.analysis.detectors import detect
from genea.analysis.scope import resolve_scope
from genea.index.embed import FakeEmbedder
from genea.tools.base import open_tools


def _kinds(findings, xref):
    return {f.kind for f in findings if f.person_ids == [f"tree.ged#{xref}"]}


def test_conflicts_and_gaps_for_a_family(actx):
    _, scope = resolve_scope(actx, "family", "tree.ged#@F1@")
    found = detect(actx, scope)
    assert {"born_after_mother_death", "missing_sex", "uncited_event", "missing_death"} <= _kinds(found, "@I5@")
    assert "siblings_too_close" in _kinds(found, "@I4@")
    assert "missing_parents" in _kinds(found, "@I1@")
    assert not {"missing_death", "uncited_event"} & _kinds(found, "@I1@")
    assert found[0].severity == "error"
    assert [f.severity for f in found] == sorted((f.severity for f in found),
                                                 key=["error", "needs_review", "info"].index)
    assert found[0].label == "Anna Weiss (tree.ged#@I5@)"


def test_probably_deceased_without_death(actx):
    _, scope = resolve_scope(actx, "person", "tree.ged#@I6@")
    found = detect(actx, scope)
    [death] = [f for f in found if f.kind == "missing_death"]
    assert death.severity == "info" and "1820" in death.message
    assert death.sources[0].file == "tree.ged" and death.sources[0].line


def test_document_mention_without_dates(actx):
    _, scope = resolve_scope(actx, "person", "tree.ged#@I7@")
    found = detect(actx, scope)
    [mention] = [f for f in found if f.kind == "mentioned_without_dates"]
    assert mention.severity == "needs_review"
    assert mention.sources[0].file == "docs/zapis.txt"
    assert "missing_birth" in {f.kind for f in found}


def test_ids_do_not_depend_on_line_numbers(tmp_path):
    shifted = TREE.replace("0 @I1@ INDI\n", "0 @I1@ INDI\n1 NOTE posunuté\n")
    ids = []
    for root, tree in ((tmp_path / "a", TREE), (tmp_path / "b", shifted)):
        ctx = open_tools(make_analysis_kb(root, tree), FakeEmbedder())
        _, scope = resolve_scope(ctx, "family", "tree.ged#@F1@")
        ids.append({f.id for f in detect(ctx, scope)})
        ctx.conn.close()
    assert ids[0] == ids[1] and ids[0]
