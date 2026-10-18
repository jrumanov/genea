"""Call rate and per-chromosome counts of an imported file."""

from __future__ import annotations

from collections import Counter

from genea.dna.store import QC, DnaError, Row

CALL_RATE_WARNING = 0.95


def _chrom_order(item: tuple[str, int]) -> tuple[int, int | str]:
    chrom = item[0]
    return (0, int(chrom)) if chrom.isdigit() else (1, chrom)


def compute_qc(rows: list[Row]) -> QC:
    if not rows:
        raise DnaError("The file contains no SNPs.")
    by_chrom = Counter(r[1] for r in rows if r[3])
    called = sum(by_chrom.values())
    rate = called / len(rows)
    warnings = []
    if rate < CALL_RATE_WARNING:
        warnings.append(f"Low call rate {rate:.1%} (below {CALL_RATE_WARNING:.0%}); results may be unreliable.")
    return QC(total=len(rows), called=called, call_rate=round(rate, 4),
              by_chrom=dict(sorted(by_chrom.items(), key=_chrom_order)), warnings=warnings)
