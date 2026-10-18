"""Synthetic MyHeritage raw DNA data for the tests: invented genotypes, never a real file."""

from __future__ import annotations

import random
from pathlib import Path

from genea.dna import store
from genea.kb import KnowledgeBase

# (GRCh37 position, allele) from yhaplo's ISOGG 2016 table: derived alleles of M168, M89, M9, M45, M207, M173,
# M343, M269, L23, L51, P311, P312 and ancestral alleles of M170, M253, M198, M458. yhaplo calls this R-P312.
Y_R1B = [(14813991, "T"), (21917313, "T"), (21730257, "G"), (21867787, "A"), (15581983, "G"), (15026424, "C"),
         (2887824, "A"), (22739367, "C"), (6753511, "A"), (8502236, "A"), (18248698, "G"), (22157311, "A"),
         (14847792, "A"), (15022707, "C"), (15030752, "C"), (24366464, "A")]
MT_POSITIONS = (73, 263, 750, 1438, 2706, 4769, 7028, 8860, 15326, 16519)


def rows_for(sex: str, seed: int = 1) -> list[store.Row]:
    """300 autosomal SNPs (every 50th a no-call, so the call rate stays above 95%), 200 X SNPs (a third heterozygous for a woman), the R1b Y SNPs for a man,
    ten homoplasmic MT calls and one heteroplasmic MT call at 16000."""
    r = random.Random(seed)
    rows: list[store.Row] = [(f"rs{1000 + i}", "1", 10_000 + i * 100,
                              None if i % 50 == 0 else r.choice(["AA", "AG", "GG"])) for i in range(300)]
    for i in range(200):
        het = sex == "female" and i % 3 == 0
        rows.append((f"rs{5000 + i}", "X", 3_000_000 + i * 100,
                     r.choice(["AG", "CT"]) if het else r.choice(["AA", "GG"])))
    if sex == "male":
        rows += [(f"rs{8000 + i}", "Y", pos, base * 2) for i, (pos, base) in enumerate(Y_R1B)]
    rows += [(f"i{900 + i}", "MT", pos, "AA") for i, pos in enumerate(MT_POSITIONS)]
    rows.append(("i999", "MT", 16000, "AG"))
    return rows


def write_csv(path: Path, sex: str = "male", build: int = 37, rows: list[store.Row] | None = None) -> Path:
    rows = rows_for(sex) if rows is None else rows
    lines = ["# MyHeritage DNA raw data.",
             "# This file was generated for the genea tests.",
             "# The genotype is reported on the forward (+) strand with respect to the human reference "
             f"build {build}.",
             "RSID,CHROMOSOME,POSITION,RESULT"]
    lines += [f'"{rsid}","{chrom}","{pos}","{gt or "--"}"' for rsid, chrom, pos, gt in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def add_person(kb: KnowledgeBase, slug: str = "tree.ged-I12", gedcom_id: str = "tree.ged#@I12@",
               sex: str = "male", rows: list[store.Row] | None = None) -> store.PersonMeta:
    rows = rows_for(sex) if rows is None else rows
    called = sum(1 for r in rows if r[3])
    meta = store.PersonMeta(slug=slug, gedcom_id=gedcom_id, source_name="MyHeritage_raw_dna_data.csv", build=37,
                            sex=sex, qc=store.QC(total=len(rows), called=called, call_rate=called / len(rows),
                                                 by_chrom={}),
                            imported_at="2026-10-15T10:00:00")
    store.save_person(kb, meta, rows)
    return meta
