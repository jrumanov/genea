import pytest
from fixtures.analysis_tree import TREE, event_line, make_analysis_kb
from typer.testing import CliRunner

from genea.analysis import api, store
from genea.cli.main import app
from genea.index.embed import FakeEmbedder
from genea.tools.base import ToolError, open_tools

CITE = ["[docs/zapis.txt]"]
runner = CliRunner()


def _family(ctx):
    return api.analyze(ctx, "family", "tree.ged#@F1@").name


def _changed(diff_text, sign):
    other = "---" if sign == "-" else "+++"
    return [line for line in diff_text.splitlines() if line.startswith(sign) and not line.startswith(other)]


def test_update_event_replaces_the_date_and_marks_it(actx, analysis_kb):
    name = _family(actx)
    line = event_line(actx, "@I3@", "BIRT")
    api.propose(actx, name, "update_event", {"person_id": "tree.ged#@I3@", "line": line, "date": "2 JAN 1878"},
                CITE, "the register says 2 January")
    before = (analysis_kb.root / "tree.ged").read_bytes()
    result = api.build_iteration(actx, name)
    assert (analysis_kb.root / "tree.ged").read_bytes() == before
    assert result.version == 1 and result.applied == ["p-1"] and result.stale == []
    assert result.iteration == f".genea/analyses/{name}/v1.ged"
    diff = (analysis_kb.root / result.diff).read_text(encoding="utf-8")
    assert _changed(diff, "-") == ["-2 DATE 1 JAN 1878"]
    added = _changed(diff, "+")
    assert added[0] == "+2 DATE 2 JAN 1878"
    assert added[1].startswith("+2 NOTE GENEA v1 ") and " p-1 (user): " in added[1]
    assert "date -> 2 JAN 1878" in added[1] and "Base value: 1 JAN 1878, Brno" in added[1]
    md = (analysis_kb.root / result.report).read_text(encoding="utf-8")
    assert "```diff" in md and "+2 DATE 2 JAN 1878" in md
    assert store.load(analysis_kb, name).iterations == 1


def test_other_operations(actx, analysis_kb):
    name = _family(actx)
    api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I1@", "text": "Mlynár"}, CITE, "why")
    api.propose(actx, name, "add_source_citation", {"person_id": "tree.ged#@I1@", "source": "Matrika Brno 1850",
                                                    "page": "f. 12", "event_type": "DEAT"}, CITE, "why")
    api.propose(actx, name, "add_event", {"person_id": "tree.ged#@I4@", "type": "BAPM", "date": "3 JUN 1878",
                                          "place": "Brno"}, CITE, "why")
    person = api.propose(actx, name, "add_person", {"given": "Mária", "surname": "Weiss", "sex": "F",
                                                    "birth_date": "1883"}, CITE, "why")
    api.propose(actx, name, "link_child", {"family_id": "tree.ged#@F1@",
                                           "child_id": f"proposal:{person.proposal_id}"}, CITE, "why")
    result = api.build_iteration(actx, name)
    assert sorted(result.applied) == ["p-1", "p-2", "p-3", "p-4", "p-5"] and result.stale == []
    text = (analysis_kb.root / result.iteration).read_text(encoding="utf-8")
    assert "1 NOTE Mlynár\n2 CONT\n2 CONT Source: [docs/zapis.txt]\n1 NOTE GENEA v1" in text
    assert "1 DEAT\n2 DATE 1910\n2 SOUR Matrika Brno\n2 SOUR Matrika Brno 1850\n3 PAGE f. 12\n2 NOTE GENEA v1" in text
    assert "1 BAPM\n2 DATE 3 JUN 1878\n2 PLAC Brno\n2 NOTE GENEA v1" in text
    assert "0 @GENEA1@ INDI\n1 NAME Mária /Weiss/\n1 SEX F\n1 BIRT\n2 DATE 1883\n1 NOTE GENEA v1" in text
    assert "1 FAMC @F1@\n0 TRLR" in text  # written by link_child, at the end of the new record
    assert "1 CHIL @I5@\n1 CHIL @GENEA1@\n1 NOTE GENEA v1" in text
    assert text.index("0 @GENEA1@ INDI") < text.index("0 TRLR")


def test_check_notes_and_dismissals(actx, analysis_kb):
    name = _family(actx)
    first = api.build_iteration(actx, name)
    assert first.checks == 2  # born after the mother's death, siblings too close; gaps get none
    text = (analysis_kb.root / first.iteration).read_text(encoding="utf-8")
    assert text.count("1 NOTE GENEA CHECK v1") == 2
    finding = next(f for f in store.load(analysis_kb, name).findings if f.kind == "born_after_mother_death")
    api.dismiss(actx, name, finding.id, "known")
    second = api.build_iteration(actx, name)
    assert second.version == 2 and second.checks == 1
    text = (analysis_kb.root / second.iteration).read_text(encoding="utf-8")
    assert "GENEA v1" not in text and "GENEA CHECK v1" not in text  # always rebuilt from the real tree
    assert finding.id not in text


def test_changed_tree_makes_proposals_stale_not_wrong(actx, analysis_kb):
    name = _family(actx)
    line = event_line(actx, "@I3@", "BIRT")
    api.propose(actx, name, "update_event", {"person_id": "tree.ged#@I3@", "line": line, "date": "2 JAN 1878"},
                CITE, "why")
    api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I4@", "text": "x"}, CITE, "why")
    api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I5@", "text": "y"}, CITE, "why")
    path = analysis_kb.root / "tree.ged"
    text = path.read_text(encoding="utf-8").replace("2 DATE 1 JAN 1878", "2 DATE 3 JAN 1878")
    text = text[:text.index("0 @I5@ INDI")] + text[text.index("0 @I6@ INDI"):]  # I5 deleted in Gramps
    path.write_text(text, encoding="utf-8")
    result = api.build_iteration(actx, name)
    assert result.applied == ["p-2"] and set(result.stale) == {"p-1", "p-3"}
    reasons = {p.id: p.stale_reason for p in store.load(analysis_kb, name).proposals}
    assert "no longer in the record" in reasons["p-1"] and "no longer in the tree" in reasons["p-3"]
    assert "changed since the analysis" in result.message
    built = (analysis_kb.root / result.iteration).read_text(encoding="utf-8")
    assert "2 DATE 3 JAN 1878" in built and "2 JAN 1878" not in built


def test_shifted_duplicate_event_is_stale(tmp_path):
    tree = TREE.replace("1 BIRT\n2 DATE 1 JUN 1878\n", "1 BIRT\n2 DATE 1 JUN 1878\n1 BIRT\n2 DATE 1 JUN 1878\n")
    kb = make_analysis_kb(tmp_path / "dup", tree)
    ctx = open_tools(kb, FakeEmbedder())
    name = api.analyze(ctx, "person", "tree.ged#@I4@").name
    api.propose(ctx, name, "update_event", {"person_id": "tree.ged#@I4@", "line": event_line(ctx, "@I4@", "BIRT"),
                                            "date": "2 JUN 1878"}, CITE, "why")
    path = kb.root / "tree.ged"
    path.write_text(path.read_text(encoding="utf-8").replace("0 @I4@ INDI\n", "0 @I4@ INDI\n1 NOTE posun\n"),
                    encoding="utf-8")
    result = api.build_iteration(ctx, name)
    ctx.conn.close()
    assert result.stale == ["p-1"]
    assert "more than once" in store.load(kb, name).proposals[0].stale_reason


def test_crlf_and_bom_are_kept(tmp_path):
    kb = make_analysis_kb(tmp_path / "crlf", newline="\r\n", bom=b"\xef\xbb\xbf")
    ctx = open_tools(kb, FakeEmbedder())
    name = api.analyze(ctx, "person", "tree.ged#@I3@").name
    api.propose(ctx, name, "add_note", {"person_id": "tree.ged#@I3@", "text": "Poznámka"}, CITE, "why")
    result = api.build_iteration(ctx, name)
    ctx.conn.close()
    data = (kb.root / result.iteration).read_bytes()
    assert data.startswith(b"\xef\xbb\xbf") and b"\n" not in data.replace(b"\r\n", b"")
    assert "1 NOTE Poznámka\r\n2 CONT\r\n2 CONT Source: [docs/zapis.txt]\r\n1 NOTE GENEA v1".encode() in data


def test_cp1250_tree(tmp_path):
    kb = make_analysis_kb(tmp_path / "ansi", TREE.replace("1 CHAR UTF-8", "1 CHAR ANSI"), encoding="cp1250")
    ctx = open_tools(kb, FakeEmbedder())
    name = api.analyze(ctx, "person", "tree.ged#@I3@").name
    api.propose(ctx, name, "update_event", {"person_id": "tree.ged#@I3@", "line": event_line(ctx, "@I3@", "BIRT"),
                                            "date": "2 JAN 1878"}, CITE, "matrika")
    api.propose(ctx, name, "add_note", {"person_id": "tree.ged#@I3@", "text": "Имя"}, CITE, "why")
    result = api.build_iteration(ctx, name)
    ctx.conn.close()
    assert result.applied == ["p-1"] and result.stale == ["p-2"]
    assert "cannot be written" in store.load(kb, name).proposals[1].stale_reason
    text = (kb.root / result.iteration).read_bytes().decode("cp1250")
    assert "date -> 2 JAN 1878" in text and "Ján /Weiss/" in text


def test_ansel_tree_is_refused(actx, analysis_kb):
    name = _family(actx)
    path = analysis_kb.root / "tree.ged"
    path.write_text(path.read_text(encoding="utf-8").replace("1 CHAR UTF-8", "1 CHAR ANSEL"), encoding="utf-8")
    with pytest.raises(ToolError, match="UTF-8"):
        api.build_iteration(actx, name)


def test_cli_build(analysis_kb, monkeypatch, actx):
    monkeypatch.setattr("genea.cli.common.get_embedder", lambda kb: FakeEmbedder())
    name = _family(actx)
    result = runner.invoke(app, ["--kb", str(analysis_kb.root), "analysis", "build", name])
    assert result.exit_code == 0, result.output
    assert "v1" in result.output and (analysis_kb.analyses_dir / name / "v1.ged").is_file()


def _files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_writes_only_inside_the_analysis_folder(actx, analysis_kb):
    name = _family(actx)
    api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I1@", "text": "x"}, CITE, "why")
    before = _files(analysis_kb.root)
    api.build_iteration(actx, name)
    after = _files(analysis_kb.root)
    changed = {k for k in after if before.get(k) != after[k]} | (before.keys() - after.keys())
    assert changed and all(k.startswith(f".genea/analyses/{name}/") for k in changed)


def test_two_updates_of_one_field_second_is_stale(actx, analysis_kb):
    name = _family(actx)
    line = event_line(actx, "@I4@", "BIRT")  # no PLAC: a second place must not add a second PLAC line
    api.propose(actx, name, "update_event", {"person_id": "tree.ged#@I4@", "line": line, "place": "Brno"},
                CITE, "why")
    api.propose(actx, name, "update_event", {"person_id": "tree.ged#@I4@", "line": line, "place": "Viedeň"},
                CITE, "why")
    result = api.build_iteration(actx, name)
    assert result.applied == ["p-1"] and result.stale == ["p-2"]
    reason = store.load(analysis_kb, name).proposals[1].stale_reason
    assert "place of the BIRT event is already changed by p-1" in reason
    text = (analysis_kb.root / result.iteration).read_text(encoding="utf-8")
    assert "Viedeň" not in text and "1 BIRT\n2 PLAC Brno\n2 DATE 1 JUN 1878\n2 NOTE GENEA v1" in text


def test_date_and_place_updates_of_one_event_both_apply(actx, analysis_kb):
    name = _family(actx)
    line = event_line(actx, "@I3@", "BIRT")
    api.propose(actx, name, "update_event", {"person_id": "tree.ged#@I3@", "line": line, "date": "2 JAN 1878"},
                CITE, "why")
    api.propose(actx, name, "update_event", {"person_id": "tree.ged#@I3@", "line": line, "place": "Viedeň"},
                CITE, "why")
    result = api.build_iteration(actx, name)
    assert result.applied == ["p-1", "p-2"] and result.stale == []
    text = (analysis_kb.root / result.iteration).read_text(encoding="utf-8")
    assert "1 BIRT\n2 DATE 2 JAN 1878\n2 PLAC Viedeň\n" in text and text.count("2 NOTE GENEA v1") == 2


def test_deleted_person_skips_its_check_note(actx, analysis_kb):
    name = _family(actx)
    api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I5@", "text": "y"}, CITE, "why")
    api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I3@", "text": "z"}, CITE, "why")
    path = analysis_kb.root / "tree.ged"
    text = path.read_text(encoding="utf-8")
    path.write_text(text[:text.index("0 @I5@ INDI")] + text[text.index("0 @I6@ INDI"):], encoding="utf-8")
    result = api.build_iteration(actx, name)
    assert result.applied == ["p-2"] and result.stale == ["p-1"]
    assert result.checks == 1  # I5's CHECK (born after the mother's death) has no record left
    assert "no longer in the tree" in store.load(analysis_kb, name).proposals[0].stale_reason


def test_long_marker_is_split_into_conc_lines(actx, analysis_kb):
    name = _family(actx)
    api.propose(actx, name, "add_note", {"person_id": "tree.ged#@I1@", "text": "x"}, CITE, "slovo " * 80)
    result = api.build_iteration(actx, name)
    lines = (analysis_kb.root / result.iteration).read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("1 NOTE GENEA v1"))
    assert lines[start + 1].startswith("2 CONC ")
    assert all(len(line.split(" ", 2)[2]) <= 248 for line in lines[start:start + 3])


def test_failed_proposal_leaves_no_partial_edit(tmp_path):
    kb = make_analysis_kb(tmp_path / "ansi2", TREE.replace("1 CHAR UTF-8", "1 CHAR ANSI"), encoding="cp1250")
    ctx = open_tools(kb, FakeEmbedder())
    name = api.analyze(ctx, "person", "tree.ged#@I3@").name
    api.propose(ctx, name, "update_event", {"person_id": "tree.ged#@I3@", "line": event_line(ctx, "@I3@", "BIRT"),
                                            "date": "2 JAN 1878"}, CITE, "запис")  # the note cannot be written
    result = api.build_iteration(ctx, name)
    ctx.conn.close()
    assert result.stale == ["p-1"]
    built = (kb.root / result.iteration).read_bytes().decode("cp1250")
    assert "2 DATE 1 JAN 1878" in built and "2 JAN 1878" not in built and "GENEA v1" not in built


def _shifted(kb, xref, extra):
    path = kb.root / "tree.ged"
    path.write_text(path.read_text(encoding="utf-8").replace(f"0 {xref} INDI\n", f"0 {xref} INDI\n{extra}"),
                    encoding="utf-8")


DUP_BIRT = TREE.replace("1 BIRT\n2 DATE 1 JUN 1878\n", "1 BIRT\n2 DATE 1 JUN 1878\n1 BIRT\n2 DATE 1 JUN 1878\n")


def test_duplicate_event_shifted_by_its_subtree_size_is_stale(tmp_path):
    kb = make_analysis_kb(tmp_path / "dup2", DUP_BIRT)
    ctx = open_tools(kb, FakeEmbedder())
    name = api.analyze(ctx, "person", "tree.ged#@I4@").name
    second = event_line(ctx, "@I4@", "BIRT") + 2
    api.propose(ctx, name, "update_event", {"person_id": "tree.ged#@I4@", "line": second, "date": "2 JUN 1878"},
                CITE, "why")
    _shifted(kb, "@I4@", "1 NOTE posun\n1 NOTE posun\n")  # the old line now points at the first BIRT
    result = api.build_iteration(ctx, name)
    ctx.conn.close()
    assert result.stale == ["p-1"]
    assert "more than once" in store.load(kb, name).proposals[0].stale_reason


def test_duplicate_event_in_an_unchanged_tree_uses_the_recorded_line(tmp_path):
    kb = make_analysis_kb(tmp_path / "dup3", DUP_BIRT)
    ctx = open_tools(kb, FakeEmbedder())
    name = api.analyze(ctx, "person", "tree.ged#@I4@").name
    second = event_line(ctx, "@I4@", "BIRT") + 2
    api.propose(ctx, name, "update_event", {"person_id": "tree.ged#@I4@", "line": second, "date": "2 JUN 1878"},
                CITE, "why")
    result = api.build_iteration(ctx, name)
    ctx.conn.close()
    assert result.applied == ["p-1"]
    text = (kb.root / result.iteration).read_text(encoding="utf-8")
    assert "1 BIRT\n2 DATE 1 JUN 1878\n1 BIRT\n2 DATE 2 JUN 1878\n" in text


def test_event_with_another_value_is_not_taken(tmp_path):
    tree = TREE.replace("0 @I4@ INDI\n", "0 @I4@ INDI\n1 OCCU farmer\n1 OCCU teacher\n")
    kb = make_analysis_kb(tmp_path / "occu", tree)
    ctx = open_tools(kb, FakeEmbedder())
    name = api.analyze(ctx, "person", "tree.ged#@I4@").name
    teacher = event_line(ctx, "@I4@", "OCCU") + 1
    api.propose(ctx, name, "update_event", {"person_id": "tree.ged#@I4@", "line": teacher, "date": "1900"},
                CITE, "why")
    assert store.load(kb, name).proposals[0].anchor["description"] == "teacher"
    _shifted(kb, "@I4@", "1 NOTE posun\n")  # the old line now points at OCCU farmer
    result = api.build_iteration(ctx, name)
    ctx.conn.close()
    assert result.applied == ["p-1"]
    text = (kb.root / result.iteration).read_text(encoding="utf-8")
    assert "1 OCCU farmer\n1 OCCU teacher\n2 DATE 1900\n2 NOTE GENEA v1" in text


def test_empty_place_still_fits(tmp_path):
    kb = make_analysis_kb(tmp_path / "empty", TREE.replace("2 DATE 1 JUN 1878\n", "2 DATE 1 JUN 1878\n2 PLAC \n"))
    ctx = open_tools(kb, FakeEmbedder())
    name = api.analyze(ctx, "person", "tree.ged#@I4@").name
    api.propose(ctx, name, "update_event", {"person_id": "tree.ged#@I4@", "line": event_line(ctx, "@I4@", "BIRT"),
                                            "date": "2 JUN 1878"}, CITE, "why")
    assert store.load(kb, name).proposals[0].anchor["place"] is None
    result = api.build_iteration(ctx, name)
    ctx.conn.close()
    assert result.applied == ["p-1"] and result.stale == []


def test_failed_link_of_a_new_person_leaves_no_famc(tmp_path):
    kb = make_analysis_kb(tmp_path / "ansi3", TREE.replace("1 CHAR UTF-8", "1 CHAR ANSI"), encoding="cp1250")
    ctx = open_tools(kb, FakeEmbedder())
    name = api.analyze(ctx, "family", "tree.ged#@F1@").name
    person = api.propose(ctx, name, "add_person", {"given": "Mária", "surname": "Weiss"}, CITE, "why")
    api.propose(ctx, name, "link_child", {"family_id": "tree.ged#@F1@", "child_id": f"proposal:{person.proposal_id}"},
                CITE, "запис")  # the marker cannot be written in CP1250
    result = api.build_iteration(ctx, name)
    ctx.conn.close()
    assert result.applied == ["p-1"] and result.stale == ["p-2"]
    text = (kb.root / result.iteration).read_bytes().decode("cp1250")
    assert "0 @GENEA1@ INDI" in text and "FAMC" not in text.split("0 @GENEA1@ INDI")[1]
    assert "CHIL @GENEA1@" not in text


def test_iteration_family_marriage(actx, analysis_kb):
    name = _family(actx)
    api.propose(actx, name, "add_event", {"family_id": "tree.ged#@F1@", "type": "MARR", "date": "1 JUN 1877",
                                          "note": "Zosobášení v Brne."}, CITE, "why")
    result = api.build_iteration(actx, name)
    assert result.applied == ["p-1"]
    text = (analysis_kb.root / result.iteration).read_text(encoding="utf-8")
    expected = ["1 MARR", "2 DATE 1 JUN 1877", "2 NOTE Zosobášení v Brne.", "3 CONT", "3 CONT Source: [docs/zapis.txt]",
                "2 NOTE GENEA v1"]
    assert chr(10).join(expected) in text


def test_iteration_resi_description(actx, analysis_kb):
    name = _family(actx)
    api.propose(actx, name, "add_event", {"person_id": "tree.ged#@I3@", "type": "RESI", "date": "1900",
                                          "address": "Hlavná 5, Nitra"}, CITE, "why")
    result = api.build_iteration(actx, name)
    text = (analysis_kb.root / result.iteration).read_text(encoding="utf-8")
    assert "1 RESI Address: Hlavná 5, Nitra\n2 DATE 1900" in text


def _decide(kb, name, pid, decision):
    analysis = store.load(kb, name)
    next(p for p in analysis.proposals if p.id == pid).conflict.decision = decision
    store.save(kb, analysis)


def _conflicting_birth(actx, name):
    line = event_line(actx, "@I3@", "BIRT")
    return api.propose(actx, name, "add_event", {"person_id": "tree.ged#@I3@", "type": "BIRT",
                                                 "date": "2 JAN 1878", "place": "Brno"}, CITE, "why",
                       conflict_line=line).proposal_id


def test_build_keep_skips(actx, analysis_kb):
    name = _family(actx)
    pid = _conflicting_birth(actx, name)
    _decide(analysis_kb, name, pid, "keep")
    result = api.build_iteration(actx, name)
    assert result.applied == [] and result.stale == []
    assert [p.status for p in store.load(analysis_kb, name).proposals] == ["withdrawn"]


def test_build_replace_marks_old(actx, analysis_kb):
    name = _family(actx)
    pid = _conflicting_birth(actx, name)
    _decide(analysis_kb, name, pid, "replace")
    result = api.build_iteration(actx, name)
    assert result.applied == [pid]
    text = (analysis_kb.root / result.iteration).read_text(encoding="utf-8")
    old = text.index("1 BIRT\n2 DATE 1 JAN 1878")
    marked = text.index("2 NOTE Superseded by BIRT 2 JAN 1878 in Brno (genea, p-1).")
    assert old < marked < text.index("1 BIRT\n2 DATE 2 JAN 1878")
    assert "2 DATE 1 JAN 1878\n2 PLAC Brno" in text


def test_build_both_adds_without_marking(actx, analysis_kb):
    name = _family(actx)
    pid = _conflicting_birth(actx, name)
    _decide(analysis_kb, name, pid, "both")
    result = api.build_iteration(actx, name)
    assert "Superseded" not in (analysis_kb.root / result.iteration).read_text(encoding="utf-8")
