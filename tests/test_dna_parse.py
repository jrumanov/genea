import sqlite3

import pytest
from fixtures.dna import rows_for, write_csv

from genea.dna import api, store
from genea.dna.parse import read_myheritage
from genea.dna.qc import compute_qc
from genea.dna.store import DnaError
from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.kb import init_kb

pytest.importorskip("snps")


@pytest.mark.parametrize("sex", ["male", "female"])
def test_reads_myheritage_and_infers_sex(tmp_path, sex):
    raw = read_myheritage(write_csv(tmp_path / "raw.csv", sex))
    assert raw.build == 37 and raw.sex == sex
    by_rsid = {r[0]: r for r in raw.rows}
    assert by_rsid["i999"] == ("i999", "MT", 16000, "AG")  # heteroplasmy kept, not collapsed
    assert len(by_rsid["rs5000"][3]) == 2  # X stays diploid as reported
    assert any(r[3] is None for r in raw.rows)  # "--" is a no-call
    assert ("rs8000" in by_rsid) == (sex == "male")


def test_refuses_other_files(tmp_path):
    other = tmp_path / "other.csv"
    other.write_text("# 23andMe\nrsid\tchromosome\tposition\tgenotype\nrs1\t1\t5\tAA\n", encoding="utf-8")
    with pytest.raises(DnaError, match="line 2"):
        read_myheritage(other)
    with pytest.raises(DnaError, match="build 37"):
        read_myheritage(write_csv(tmp_path / "b38.csv", build=38))
    with pytest.raises(DnaError, match="not found"):
        read_myheritage(tmp_path / "missing.csv")


def test_qc_counts_and_warns_on_low_call_rate():
    qc = compute_qc(rows_for("male"))
    assert qc.total == len(rows_for("male"))
    assert qc.by_chrom["Y"] == 16 and qc.by_chrom["MT"] == 11
    assert list(qc.by_chrom) == ["1", "MT", "X", "Y"]  # autosomes by number, then the rest by name
    low = compute_qc([("rs1", "1", 1, None), ("rs2", "1", 2, "AA")])
    assert low.call_rate == 0.5 and "Low call rate" in low.warnings[0]


def test_import_links_the_tree_person(analysis_kb, tmp_path):
    result = api.import_file(analysis_kb, write_csv(tmp_path / "MyHeritage_raw_dna_data.csv"), "tree.ged#I1")
    meta = result.meta
    assert (meta.slug, meta.gedcom_id, meta.tree_name, meta.tree_sex) == ("tree.ged-I1", "tree.ged#@I1@",
                                                                          "Karol Weiss", "M")
    assert meta.source_name == "MyHeritage_raw_dna_data.csv" and str(tmp_path) not in meta.model_dump_json()
    assert result.warnings == []
    assert store.genotypes(analysis_kb, "tree.ged-I1", ["rs8000"])["rs8000"][3] == "TT"


def test_import_warns_when_tree_sex_disagrees(analysis_kb, tmp_path):
    result = api.import_file(analysis_kb, write_csv(tmp_path / "f.csv", "female"), "tree.ged#@I1@")
    assert any("looks female" in w for w in result.warnings)


def test_import_refuses_unknown_tree_person(analysis_kb, tmp_path):
    with pytest.raises(DnaError, match="I999"):
        api.import_file(analysis_kb, write_csv(tmp_path / "m.csv"), "tree.ged#@I999@")


def test_import_without_index_warns(tmp_path):
    kb = init_kb(tmp_path / "kb")
    result = api.import_file(kb, write_csv(tmp_path / "m.csv"), "tree.ged#@I12@", slug="dad")
    assert result.meta.slug == "dad" and result.meta.tree_name is None
    assert any("not indexed" in w for w in result.warnings)


def test_reimport_needs_replace_before_parsing(analysis_kb, tmp_path, monkeypatch):
    csv = write_csv(tmp_path / "m.csv")
    api.import_file(analysis_kb, csv, "tree.ged#@I1@")
    monkeypatch.setattr(api, "read_myheritage", lambda path: pytest.fail("parsed before the --replace check"))
    with pytest.raises(DnaError, match="--replace"):
        api.import_file(analysis_kb, csv, "tree.ged#@I1@")


def test_dna_data_is_never_indexed(analysis_kb, tmp_path):
    api.import_file(analysis_kb, write_csv(tmp_path / "m.csv"), "tree.ged#@I1@")
    index_kb(analysis_kb, FakeEmbedder())
    conn = sqlite3.connect(analysis_kb.index_path)
    paths = [r[0] for r in conn.execute("SELECT path FROM files")]
    conn.close()
    assert paths and not [p for p in paths if p.startswith(".genea")]
