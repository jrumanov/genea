import subprocess
from pathlib import Path

import pytest
from fixtures.dna import MT_POSITIONS, Y_R1B, add_person, rows_for

from genea.dna import api, haplo, runtime, store
from genea.dna.store import DnaError
from genea.kb import init_kb

RCRS = "ACGT" * 4143  # 16572 bases; position p has RCRS[p - 1]


def mt(pos, base):
    return (f"i{pos}", "MT", pos, base * 2)


def test_haploid():
    assert [haplo.haploid(g) for g in ["AA", "A", "AG", None, "--", "II", "NN"]] == \
        ["A", "A", None, None, None, None, None]


def test_mt_vcf_marks_variants_and_skips_heteroplasmy():
    rows = [mt(1, "A"), mt(2, "T"), ("i3", "MT", 3, "GT"), ("i4", "MT", 4, None), mt(1, "A"), mt(20000, "A")]
    text, used, skipped, agree = haplo.mt_vcf(rows, RCRS)
    assert (used, skipped, agree) == (2, 4, 1)
    body = text.splitlines()[2:]
    assert body == ["MT\t1\t.\tA\t.\t.\tPASS\t.\tGT\t0", "MT\t2\t.\tC\tT\t.\tPASS\t.\tGT\t1"]


def test_y_genos_is_sample_major_text():
    text, used, skipped = haplo.y_genos([("a", "Y", 20, "CC"), ("b", "Y", 10, "A"), ("c", "Y", 30, "AG")])
    assert text == "ID\t10\t20\nSAMPLE\tA\tC\n" and (used, skipped) == (2, 1)
    assert haplo.y_genos([("c", "Y", 30, "AG")]) == ("", 0, 1)


def test_parsers():
    hg = '"SampleID"\t"Haplogroup"\t"Rank"\t"Quality"\t"Range"\n"SAMPLE"\t"J1b1a"\t"1"\t"0.9312"\t"70 93"\n'
    assert haplo.parse_haplogrep(hg) == ("J1b1a", 0.9312)
    assert haplo.parse_yhaplo("SAMPLE       R-P312          R-P312          R1b1a2a1a2\n") == ("R-P312", "R1b1a2a1a2")
    with pytest.raises(DnaError):
        haplo.parse_yhaplo("")


@pytest.fixture
def tools_installed(monkeypatch, tmp_path):
    """Pretend Java, Haplogrep (with an rCRS of RCRS) and yhaplo are installed."""
    exe = runtime.haplogrep_exe()
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"jar")
    runtime.rcrs_fasta().parent.mkdir(parents=True)
    runtime.rcrs_fasta().write_text(">rCRS\n" + RCRS + "\n", encoding="ascii")
    monkeypatch.setattr(runtime, "find_java", lambda run=None: runtime.JavaChoice("java", "system", "system Java 21"))
    monkeypatch.setattr(runtime, "yhaplo_version", lambda: "2.1.19")
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if "classify" in cmd:
            vcf = Path(cmd[cmd.index("--in") + 1]).read_text(encoding="ascii")
            assert "--chip" in cmd and runtime.HAPLOGREP_TREE in cmd and "\tGT\t" in vcf
            Path(cmd[cmd.index("--out") + 1]).write_text(
                '"SampleID"\t"Haplogroup"\t"Rank"\t"Quality"\n"SAMPLE"\t"H1"\t"1"\t"0.85"\n', encoding="utf-8")
        else:
            genos = Path(cmd[cmd.index("-i") + 1])
            assert genos.name == "y.genos.txt"
            out = Path(cmd[cmd.index("-o") + 1])
            out.mkdir()
            (out / "haplogroups.y.txt").write_text("SAMPLE R-P312 R-P312 R1b1a2a1a2\n", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return run, calls


def _matching_mt_rows():
    return [mt(p, RCRS[p - 1]) for p in MT_POSITIONS] + [("i999", "MT", 16000, "AG")]


def test_analyze_male_writes_both_lines(tmp_path, tools_installed):
    run, calls = tools_installed
    kb = init_kb(tmp_path / "kb")
    rows = [r for r in rows_for("male") if r[1] != "MT"] + _matching_mt_rows()
    add_person(kb, rows=rows)
    result = api.analyze(kb, "tree.ged-I12", run)
    mtdna, y = result.calls
    assert (mtdna.line, mtdna.haplogroup, mtdna.quality, mtdna.markers_used, mtdna.markers_skipped) == \
        ("mtDNA", "H1", 0.85, 10, 1)
    assert (y.line, y.haplogroup, y.detail, y.markers_used) == ("Y", "R-P312", "R1b1a2a1a2", len(Y_R1B))
    assert "Haplogrep 3.3.2" in mtdna.tool and "yhaplo 2.1.19" in y.tool
    assert store.load_haplogroups(kb, "tree.ged-I12") == result
    assert len(calls) == 2


def test_female_skips_y_without_running_yhaplo(tmp_path, tools_installed):
    run, calls = tools_installed
    kb = init_kb(tmp_path / "kb")
    rows = [r for r in rows_for("female") if r[1] != "MT"] + _matching_mt_rows()
    add_person(kb, sex="female", rows=rows)
    mtdna, y = api.analyze(kb, "tree.ged-I12", run).calls
    assert mtdna.haplogroup == "H1"
    assert y.haplogroup is None and "female" in y.skipped_reason
    assert all("classify" in c for c in calls)


def test_mt_not_in_rcrs_coordinates_is_skipped_but_y_runs(tmp_path, tools_installed):
    run, _ = tools_installed
    kb = init_kb(tmp_path / "kb")
    shifted = [mt(p, RCRS[p]) for p in MT_POSITIONS]  # every base belongs to the next position
    add_person(kb, rows=[("rs8000", "Y", Y_R1B[0][0], "TT")] + shifted)
    mtdna, y = api.analyze(kb, "tree.ged-I12", run).calls
    assert mtdna.haplogroup is None and "rCRS" in mtdna.skipped_reason
    assert y.haplogroup == "R-P312"


def test_missing_java_skips_mtdna_with_the_fix(tmp_path, tools_installed, monkeypatch):
    run, _ = tools_installed
    monkeypatch.setattr(runtime, "find_java", lambda run=None: runtime.JavaChoice(None, "none", "no Java 11+"))
    kb = init_kb(tmp_path / "kb")
    add_person(kb)
    mtdna, y = api.analyze(kb, "tree.ged-I12", run).calls
    assert "genea dna setup" in mtdna.skipped_reason and y.haplogroup == "R-P312"


def test_missing_yhaplo_skips_y_with_the_fix(tmp_path, tools_installed, monkeypatch):
    run, _ = tools_installed
    monkeypatch.setattr(runtime, "yhaplo_version", lambda: None)
    kb = init_kb(tmp_path / "kb")
    add_person(kb)
    y = api.analyze(kb, "tree.ged-I12", run).calls[1]
    assert "uv sync --extra dna" in y.skipped_reason and y.markers_used == len(Y_R1B)


def test_failing_tool_writes_nothing(tmp_path, tools_installed):
    kb = init_kb(tmp_path / "kb")
    add_person(kb, rows=_matching_mt_rows())

    def broken(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "Exception in thread main")
    with pytest.raises(DnaError, match="Haplogrep failed: Exception in thread main"):
        api.analyze(kb, "tree.ged-I12", broken)
    assert store.load_haplogroups(kb, "tree.ged-I12") is None
