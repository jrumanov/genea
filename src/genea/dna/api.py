"""DNA operations for the CLI: import a raw file and analyse haplogroups. Tools never call these."""

from __future__ import annotations

import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from genea.dna import haplo
from genea.dna.parse import read_myheritage
from genea.dna.qc import compute_qc
from genea.dna.store import (DnaError, Haplogroups, PersonMeta, check_slug, chrom_rows, normalize_gedcom_id, now,
                             person_dir, resolve_person, save_haplogroups, save_person, slug_for)
from genea.index.progress import index_state
from genea.kb import KnowledgeBase


@dataclass
class ImportResult:
    meta: PersonMeta
    warnings: list[str]


def _tree_person(kb: KnowledgeBase, gedcom_id: str) -> tuple[str | None, str | None] | None:
    """(name, sex) of the person in the index; None when the knowledge base is not indexed."""
    if index_state(kb) != "ready":  # missing, being rebuilt or interrupted: persons may be absent or partial
        return None
    ged, xref = gedcom_id.split("#", 1)
    try:
        conn = sqlite3.connect(f"{kb.index_path.as_uri()}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT given, surname, sex FROM persons WHERE ged_file = ? AND xref = ?",
                               (ged, xref)).fetchone()
        finally:
            conn.close()
    except sqlite3.OperationalError:  # no persons table yet
        return None
    if row is None:
        raise DnaError(f"No person {gedcom_id} in the indexed tree; check the id with find_person.")
    return " ".join(p for p in row[:2] if p) or None, row[2]


def import_file(kb: KnowledgeBase, csv_path: Path, gedcom_id: str, slug: str | None = None,
                replace: bool = False) -> ImportResult:
    gedcom_id = normalize_gedcom_id(gedcom_id)
    slug = slug or slug_for(gedcom_id)
    check_slug(slug)
    if person_dir(kb, slug).exists() and not replace:  # before parsing: a real file takes a while to read
        raise DnaError(f"{slug} is already imported; add --replace to import it again.")
    tree = _tree_person(kb, gedcom_id)
    raw = read_myheritage(Path(csv_path))
    qc = compute_qc(raw.rows)
    warnings = list(qc.warnings)
    name = tree_sex = None
    if tree is None:
        warnings.append("The knowledge base is not indexed (or the index is still being built), so the tree id was not checked; run `genea index` "
                        "and import again with --replace to link the name.")
    else:
        name, tree_sex = tree
        dna_sex = {"male": "M", "female": "F"}.get(raw.sex)
        if dna_sex and tree_sex in ("M", "F") and tree_sex != dna_sex:
            warnings.append(f"The tree records {name or gedcom_id} as {tree_sex}, but the DNA looks {raw.sex}; "
                            "is the file linked to the right person?")
    meta = PersonMeta(slug=slug, gedcom_id=gedcom_id, tree_name=name, tree_sex=tree_sex,
                      source_name=Path(csv_path).name, build=raw.build, sex=raw.sex, qc=qc, imported_at=now())
    save_person(kb, meta, raw.rows, replace)
    return ImportResult(meta, warnings)


def analyze(kb: KnowledgeBase, person: str, run=subprocess.run) -> Haplogroups:
    """Call the mtDNA and Y haplogroups and save them; a tool that fails leaves the old result untouched."""
    meta = resolve_person(kb, person)
    rows = chrom_rows(kb, meta.slug, ("MT", "Y"))
    with tempfile.TemporaryDirectory(prefix="genea-dna-") as tmp:
        work = Path(tmp)
        calls = [haplo.call_mtdna([r for r in rows if r[1] == "MT"], work, run),
                 haplo.call_y(meta.sex, [r for r in rows if r[1] == "Y"], work, run)]
    result = Haplogroups(analyzed_at=now(), calls=calls)
    save_haplogroups(kb, meta.slug, result)
    return result
