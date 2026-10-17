"""Read-only DNA tools shared by the local agent and the MCP server. Nothing here computes or writes."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from genea.dna.limits import MAX_RSIDS_PER_CALL, RsidBudget
from genea.dna.store import HaploCall, genotypes, load_haplogroups, resolve_person
from genea.kb import KnowledgeBase
from genea.tools.base import ToolError
from genea.tools.models import SourceRef, ToolResult

CAVEAT = ("A Y haplogroup describes only the direct paternal line and an mtDNA haplogroup only the direct maternal "
          "line, not the whole person. Calls from a genotyping chip have limited resolution (see markers_used). "
          "A haplogroup supports, but never proves, a specific link in the tree.")
_RSID = re.compile(r"^(rs|i)\d+$")


class DnaSummary(ToolResult):
    person: str
    gedcom_id: str
    tree_name: str | None = None
    sex: str
    build: int
    snps: int
    called: int
    call_rate: float
    warnings: list[str] = Field(default_factory=list)
    haplogroups: str


class HaplogroupResult(ToolResult):
    person: str
    gedcom_id: str
    analyzed_at: str
    calls: list[HaploCall]
    caveat: str


class SnpGenotype(BaseModel):
    rsid: str
    chrom: str | None = None
    pos: int | None = None
    genotype: str | None = None
    note: str | None = None


class SnpLookupResult(ToolResult):
    person: str
    results: list[SnpGenotype]
    lookups_left: int


def _source(slug: str, name: str) -> SourceRef:
    return SourceRef(file=f"dna/{slug}/{name}")


def dna_summary(kb: KnowledgeBase, person: str) -> DnaSummary:
    meta = resolve_person(kb, person)
    analysed = load_haplogroups(kb, meta.slug)
    status = (f"analysed {analysed.analyzed_at}" if analysed
              else f"not analysed yet: the user runs `genea dna analyze {meta.slug}`")
    return DnaSummary(person=meta.slug, gedcom_id=meta.gedcom_id, tree_name=meta.tree_name, sex=meta.sex,
                      build=meta.build, snps=meta.qc.total, called=meta.qc.called, call_rate=meta.qc.call_rate,
                      warnings=meta.qc.warnings, haplogroups=status, sources=[_source(meta.slug, "meta.json")])


def haplogroups(kb: KnowledgeBase, person: str) -> HaplogroupResult:
    meta = resolve_person(kb, person)
    result = load_haplogroups(kb, meta.slug)
    if result is None:
        raise ToolError(f"No haplogroups for {meta.slug} yet: the user runs `genea dna analyze {meta.slug}`.")
    return HaplogroupResult(person=meta.slug, gedcom_id=meta.gedcom_id, analyzed_at=result.analyzed_at,
                            calls=result.calls, caveat=CAVEAT, sources=[_source(meta.slug, "haplogroups.json")])


def normalize_rsids(rsids: list[str] | str) -> list[str]:
    """Lower-case, split 'rs1, rs2', drop duplicates; reject anything that is not an rsID before it costs budget."""
    items = [rsids] if isinstance(rsids, str) else list(rsids or [])
    wanted = list(dict.fromkeys(part.lower() for item in items for part in re.split(r"[\s,;]+", str(item)) if part))
    bad = [r for r in wanted if not _RSID.match(r)]
    if bad:
        raise ToolError(f"Not rsIDs: {', '.join(bad[:5])}. Use ids like rs4988235.")
    if not wanted:
        raise ToolError(f"Give 1-{MAX_RSIDS_PER_CALL} rsIDs, like rs4988235.")
    if len(wanted) > MAX_RSIDS_PER_CALL:
        raise ToolError(f"At most {MAX_RSIDS_PER_CALL} rsIDs per call ({len(wanted)} given). "
                        "This is a deliberate privacy limit.")
    return wanted


def snp_lookup(kb: KnowledgeBase, budget: RsidBudget, person: str, rsids: list[str] | str) -> SnpLookupResult:
    meta = resolve_person(kb, person)
    wanted = normalize_rsids(rsids)
    budget.take(len(wanted))
    found = genotypes(kb, meta.slug, wanted)
    results = []
    for rsid in wanted:
        row = found.get(rsid)
        if row is None:
            results.append(SnpGenotype(rsid=rsid, note="not on this chip (not tested), which is not the same as "
                                                       "no variant"))
        elif row[3] is None:
            results.append(SnpGenotype(rsid=rsid, chrom=row[1], pos=row[2], note="tested, but no call"))
        else:
            results.append(SnpGenotype(rsid=rsid, chrom=row[1], pos=row[2], genotype=row[3]))
    return SnpLookupResult(person=meta.slug, results=results, lookups_left=budget.left,
                           sources=[_source(meta.slug, "genotypes.sqlite")])
