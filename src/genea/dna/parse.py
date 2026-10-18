"""Read a MyHeritage raw DNA CSV (through snps) into rows; refuse anything else."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from genea.dna.store import DnaError, Row

HEADER = "RSID,CHROMOSOME,POSITION,RESULT"


@dataclass(frozen=True)
class RawDna:
    rows: list[Row]
    build: int
    sex: str  # "male", "female" or "unknown"


def check_header(path: Path) -> None:
    """The first line that is not a comment must be the MyHeritage header."""
    with path.open(encoding="utf-8-sig", errors="replace") as f:
        for number, line in enumerate(f, 1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if text.replace('"', "").replace(" ", "").upper() == HEADER:
                return
            raise DnaError(f"Not a MyHeritage raw DNA file: line {number} is {text[:80]!r}, "
                           f"expected the header {HEADER}.")
    raise DnaError(f"Not a MyHeritage raw DNA file: no {HEADER} header line.")


def read_myheritage(path: Path) -> RawDna:
    path = Path(path)
    if not path.is_file():
        raise DnaError(f"File not found: {path}")
    check_header(path)
    try:
        from snps import SNPs
    except ImportError as exc:
        raise DnaError("DNA import needs the optional extra: uv sync --extra dna") from exc
    # Keep X, Y and MT as reported: collapsing would hide heterozygous Y and heteroplasmic MT calls.
    data = SNPs(str(path), deduplicate_XY_chrom=False, deduplicate_MT_chrom=False)
    if data.source != "MyHeritage":
        raise DnaError(f"Not a MyHeritage raw DNA file (snps detected {data.source or 'nothing'}).")
    if data.build != 37:
        raise DnaError(f"The file uses genome build {data.build or 'unknown'}; genea needs build 37 (GRCh37) "
                       "and does not convert coordinates.")
    frame = data.snps
    rows: list[Row] = [(str(rsid).lower(), str(chrom), int(pos), g if isinstance(g, str) and g else None)
                       for rsid, chrom, pos, g in zip(frame.index, frame["chrom"], frame["pos"], frame["genotype"],
                                                      strict=True)]
    if not rows:
        raise DnaError("The file contains no SNPs.")
    return RawDna(rows, 37, {"Male": "male", "Female": "female"}.get(data.sex, "unknown"))
