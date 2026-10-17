import pytest
from fixtures.dna import add_person

from genea.dna import store
from genea.dna.store import DnaError, HaploCall, Haplogroups
from genea.kb import init_kb


@pytest.fixture
def kb(tmp_path):
    return init_kb(tmp_path / "kb")


def test_slug_and_gedcom_id():
    assert store.normalize_gedcom_id(" tree.ged#I12 ") == "tree.ged#@I12@"
    assert store.slug_for("tree.ged#@I12@") == "tree.ged-I12"
    with pytest.raises(DnaError, match="tree.ged#@I12@"):
        store.normalize_gedcom_id("I12")
    for bad in ("..", ".", "a/b", ""):
        with pytest.raises(DnaError):
            store.check_slug(bad)


def test_save_and_resolve_by_slug_or_gedcom_id(kb):
    add_person(kb)
    assert store.resolve_person(kb, "tree.ged-I12").gedcom_id == "tree.ged#@I12@"
    assert store.resolve_person(kb, "tree.ged#I12").slug == "tree.ged-I12"
    with pytest.raises(DnaError, match="tree.ged-I12"):
        store.resolve_person(kb, "nobody")
    assert (kb.dna_dir / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert [m.slug for m in store.list_people(kb)] == ["tree.ged-I12"]


def test_genotype_lookup_and_rows(kb):
    add_person(kb)
    found = store.genotypes(kb, "tree.ged-I12", ["rs8000", "rs1000", "rs404"])
    assert found["rs8000"] == ("rs8000", "Y", 14813991, "TT")
    assert "rs404" not in found
    mt = store.chrom_rows(kb, "tree.ged-I12", ("MT",))
    assert [r[2] for r in mt] == sorted(r[2] for r in mt) and len(mt) == 11


def test_existing_person_needs_replace(kb):
    add_person(kb)
    with pytest.raises(DnaError, match="--replace"):
        add_person(kb)


def test_replace_drops_old_analysis(kb):
    add_person(kb)
    store.save_haplogroups(kb, "tree.ged-I12", Haplogroups(analyzed_at="x", calls=[HaploCall(line="Y")]))
    meta = store.load_meta(kb, "tree.ged-I12")
    store.save_person(kb, meta, [("rs1", "1", 5, "AA")], replace=True)
    assert store.load_haplogroups(kb, "tree.ged-I12") is None
    assert store.genotypes(kb, "tree.ged-I12", ["rs8000"]) == {}
    assert not any(p.name.startswith(".tree.ged-I12") for p in kb.dna_dir.iterdir())


def test_failed_write_leaves_no_person(kb, monkeypatch):
    def boom(path, rows):
        raise OSError("disk full")
    monkeypatch.setattr(store, "_write_genotypes", boom)
    with pytest.raises(OSError):
        add_person(kb)
    assert store.list_people(kb) == []
    assert not (kb.dna_dir / "tree.ged-I12").exists()


def test_citation_exists(kb):
    add_person(kb)
    assert store.citation_exists(kb, "dna/tree.ged-I12/meta.json")
    assert store.citation_exists(kb, "dna/tree.ged-I12/genotypes.sqlite")
    assert not store.citation_exists(kb, "dna/tree.ged-I12/haplogroups.json")  # not analysed yet
    assert not store.citation_exists(kb, "dna/../config.toml")
    assert not store.citation_exists(kb, "dna/../meta.json")


def test_two_kbs_do_not_share_people(tmp_path):
    a, b = init_kb(tmp_path / "a"), init_kb(tmp_path / "b")
    add_person(a)
    assert store.list_people(b) == []
    with pytest.raises(DnaError):
        store.resolve_person(b, "tree.ged-I12")
