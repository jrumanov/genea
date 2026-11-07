import pytest
from fixtures.analysis_tree import event_line

from genea.analysis import api, store
from genea.analysis.bundle import parse_bundle, render_bundle
from genea.tools.base import ToolError

CITE = ["[docs/zapis.txt]"]
ME = "tree.ged#@I3@"


@pytest.fixture
def analysis(actx):
    name = api.analyze(actx, "family", "tree.ged#@F1@").name
    api.propose(actx, name, "add_event", {"person_id": ME, "type": "BAPM", "date": "3 JAN 1878"}, CITE, "why")
    api.propose(actx, name, "add_event", {"person_id": ME, "type": "RESI", "address": "Hlavná 5, Nitra"},
                CITE, "why")
    api.propose(actx, name, "add_note", {"person_id": ME, "text": "Bol mlynár."}, CITE, "why")
    api.propose(actx, name, "add_event", {"person_id": ME, "type": "BIRT", "date": "2 JAN 1878"}, CITE, "why",
                conflict_line=event_line(actx, "@I3@", "BIRT"))
    api.propose(actx, name, "add_note", {"person_id": ME, "text": "Leads: Jozef Weiss, brat"}, CITE, "why")
    return store.load(actx.kb, name)


def _drop(text, pid):
    """The bundle without the section of one item."""
    out, skipping = [], False
    for line in text.splitlines():
        if line.startswith("#"):
            skipping = line.startswith(f"### {pid}:")
        if not skipping:
            out.append(line)
    return "\n".join(out) + "\n"


def test_render_parse_roundtrip(analysis):
    text = render_bundle(analysis)
    headers = [line for line in text.splitlines() if line.startswith("## ")]
    assert headers == ["## Events", "## Residences", "## Notes", "## Conflicts", "## Leads"]
    assert "Existing: BIRT 1 JAN 1878 in Brno" in text and "decision: undecided" in text
    assert "Bol mlynár.\n\nSource: [docs/zapis.txt]" in text
    kept, decisions = parse_bundle(text, analysis)
    assert kept == {"p-1", "p-2", "p-3", "p-4", "p-5"} and decisions == {"p-4": "undecided"}


def test_parse_dropped_section(analysis):
    kept, _ = parse_bundle(_drop(render_bundle(analysis), "p-3"), analysis)
    assert kept == {"p-1", "p-2", "p-4", "p-5"}


def test_parse_reads_a_decision(analysis):
    text = render_bundle(analysis).replace("decision: undecided", "decision: Replace")
    assert parse_bundle(text, analysis)[1] == {"p-4": "replace"}


def test_parse_bad_decision_names_line(analysis):
    text = render_bundle(analysis).replace("decision: undecided", "decision: maybe")
    line = text.splitlines().index("decision: maybe") + 1
    with pytest.raises(ToolError, match=f"line {line}.*maybe"):
        parse_bundle(text, analysis)


def test_parse_unknown_id(analysis):
    text = render_bundle(analysis) + "\n### p-9: Add a note\n"
    with pytest.raises(ToolError, match="line \\d+.*p-9"):
        parse_bundle(text, analysis)


def test_parse_duplicate_id(analysis):
    text = render_bundle(analysis) + "\n### p-1: again\n"
    with pytest.raises(ToolError, match="line \\d+.*p-1.*twice"):
        parse_bundle(text, analysis)


@pytest.mark.parametrize(("old", "new"), [("## Events", "## Evnts"), ("### p-2:", "###p-2:"), ("### p-2:", "#### p-2:")],
                         ids=["section", "no-space", "level"])
def test_parse_mangled_header_names_line(analysis, old, new):
    text = render_bundle(analysis).replace(old, new, 1)
    line = next(i for i, text_line in enumerate(text.splitlines(), 1) if text_line.startswith(new))
    with pytest.raises(ToolError, match=f"line {line}"):
        parse_bundle(text, analysis)


def test_decision_on_an_item_without_conflict(analysis):
    text = render_bundle(analysis).replace("### p-3:", "decision: keep\n### p-3:", 1)
    with pytest.raises(ToolError, match="line \\d+.*no conflict"):
        parse_bundle(text, analysis)


def test_note_text_with_hash_lines_survives(actx):
    name = api.analyze(actx, "family", "tree.ged#@F1@").name
    api.propose(actx, name, "add_note", {"person_id": ME, "text": "Prvý riadok\n### p-9: x\ndecision: keep"},
                CITE, "why")
    analysis = store.load(actx.kb, name)
    assert parse_bundle(render_bundle(analysis), analysis) == ({"p-1"}, {})
