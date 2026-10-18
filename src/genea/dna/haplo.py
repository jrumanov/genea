"""mtDNA (Haplogrep 3) and Y (yhaplo) haplogroups from a person's MT and Y rows.

Only clean haploid calls are used: no-calls, heterozygous Y and heteroplasmic MT calls are skipped and counted.
"""

from __future__ import annotations

import csv
import io
import subprocess
import sys
from pathlib import Path

from genea.dna import runtime
from genea.dna.store import DnaError, HaploCall, Row

RCRS_MIN_AGREEMENT = 0.75  # real mtDNA matches rCRS at most chip positions; shifted coordinates match ~25%
YHAPLO_TREE = "ISOGG 2016.01.04"


def haploid(genotype: str | None) -> str | None:
    """'AA' or 'A' -> 'A'; a no-call, 'AG' or anything but one of A/C/G/T -> None."""
    if not genotype or len(set(genotype)) != 1 or genotype[0] not in "ACGT":
        return None
    return genotype[0]


def read_fasta(path: Path) -> str:
    lines = path.read_text(encoding="ascii").splitlines()
    return "".join(line.strip() for line in lines if not line.startswith(">")).upper()


def mt_vcf(rows: list[Row], rcrs: str) -> tuple[str, int, int, int]:
    """A single-sample chip VCF against rCRS: (text, positions used, rows skipped, positions equal to rCRS)."""
    lines = ["##fileformat=VCFv4.2", "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE"]
    used = skipped = agree = 0
    seen: set[int] = set()
    for _rsid, _chrom, pos, genotype in sorted(rows, key=lambda r: r[2]):
        base = haploid(genotype)
        ref = rcrs[pos - 1] if 1 <= pos <= len(rcrs) else "N"
        if base is None or ref not in ("A", "C", "G", "T") or pos in seen:
            skipped += 1
            continue
        seen.add(pos)
        used += 1
        if base == ref:
            agree += 1
            lines.append(f"MT\t{pos}\t.\t{ref}\t.\t.\tPASS\t.\tGT\t0")
        else:
            lines.append(f"MT\t{pos}\t.\t{ref}\t{base}\t.\tPASS\t.\tGT\t1")
    return "\n".join(lines) + "\n", used, skipped, agree


def y_genos(rows: list[Row]) -> tuple[str, int, int]:
    """yhaplo's sample-major text (row 0: GRCh37 positions): (text, positions used, rows skipped)."""
    positions, alleles, skipped, seen = [], [], 0, set()
    for _rsid, _chrom, pos, genotype in sorted(rows, key=lambda r: r[2]):
        base = haploid(genotype)
        if base is None or pos in seen:
            skipped += 1
            continue
        seen.add(pos)
        positions.append(str(pos))
        alleles.append(base)
    if not positions:
        return "", 0, skipped
    return "ID\t" + "\t".join(positions) + "\nSAMPLE\t" + "\t".join(alleles) + "\n", len(positions), skipped


def parse_haplogrep(text: str) -> tuple[str, float]:
    rows = list(csv.DictReader(io.StringIO(text), delimiter="\t"))
    if not rows or not rows[0].get("Haplogroup"):
        raise DnaError("Haplogrep wrote no haplogroup.")
    return rows[0]["Haplogroup"], float(rows[0]["Quality"])


def parse_yhaplo(text: str) -> tuple[str, str]:
    """yhaplo's line 'SAMPLE  R-P312  R-P312  R1b1a2a1a2' -> ('R-P312', 'R1b1a2a1a2')."""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4:
            return parts[2], parts[3]
    raise DnaError("yhaplo wrote no haplogroup.")


def _tail(done: subprocess.CompletedProcess) -> str:
    text = ((done.stderr or "") + (done.stdout or "")).strip()
    return text[-500:] or f"exit code {done.returncode}"


def call_mtdna(rows: list[Row], work: Path, run=subprocess.run) -> HaploCall:
    tool = f"Haplogrep {runtime.HAPLOGREP_VERSION} ({runtime.HAPLOGREP_TREE})"
    java = runtime.find_java()
    if java.path is None:
        return HaploCall(line="mtDNA", skipped_reason=f"{java.detail}; run `genea dna setup`.", tool=tool)
    if not (runtime.haplogrep_exe().is_file() and runtime.rcrs_fasta().is_file()):
        return HaploCall(line="mtDNA", skipped_reason="Haplogrep is not installed; run `genea dna setup`.", tool=tool)
    text, used, skipped, agree = mt_vcf(rows, read_fasta(runtime.rcrs_fasta()))
    if used == 0:
        return HaploCall(line="mtDNA", markers_skipped=skipped, skipped_reason="The file has no usable mtDNA calls.",
                         tool=tool)
    if agree / used < RCRS_MIN_AGREEMENT:
        return HaploCall(line="mtDNA", markers_used=used, markers_skipped=skipped, tool=tool,
                         skipped_reason=f"Only {agree / used:.0%} of the mtDNA calls match the rCRS reference, so "
                                        "the file's mtDNA positions do not look like rCRS coordinates; no "
                                        "haplogroup was called.")
    vcf, out = work / "mt.vcf", work / "mt.txt"
    vcf.write_text(text, encoding="ascii")
    cmd = [java.path, "-jar", str(runtime.haplogrep_exe()), "classify", "--in", str(vcf),
           "--tree", runtime.HAPLOGREP_TREE, "--chip", "--out", str(out)]
    done = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
               cwd=runtime.haplogrep_dir())
    if done.returncode != 0 or not out.is_file():
        raise DnaError(f"Haplogrep failed: {_tail(done)}")
    haplogroup, quality = parse_haplogrep(out.read_text(encoding="utf-8"))
    return HaploCall(line="mtDNA", haplogroup=haplogroup, quality=quality, markers_used=used,
                     markers_skipped=skipped, tool=tool)


def call_y(sex: str, rows: list[Row], work: Path, run=subprocess.run) -> HaploCall:
    version = runtime.yhaplo_version()
    tool = f"yhaplo {version} ({YHAPLO_TREE})" if version else f"yhaplo ({YHAPLO_TREE})"
    if sex == "female":
        return HaploCall(line="Y", skipped_reason="The DNA looks female: there is no Y chromosome to classify.",
                         tool=tool)
    text, used, skipped = y_genos(rows)
    if used == 0:
        return HaploCall(line="Y", markers_skipped=skipped, skipped_reason="The file has no usable Y calls.",
                         tool=tool)
    if version is None:
        return HaploCall(line="Y", markers_used=used, markers_skipped=skipped, tool=tool,
                         skipped_reason="yhaplo is not installed; run `uv sync --extra dna`.")
    genos, out_dir = work / "y.genos.txt", work / "yhaplo"
    genos.write_text(text, encoding="ascii")
    cmd = [sys.executable, "-c", "from yhaplo.cli.yhaplo import main; main()", "-i", str(genos), "-o", str(out_dir)]
    done = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    result = out_dir / "haplogroups.y.txt"
    if done.returncode != 0 or not result.is_file():
        raise DnaError(f"yhaplo failed: {_tail(done)}")
    haplogroup, isogg = parse_yhaplo(result.read_text(encoding="utf-8"))
    return HaploCall(line="Y", haplogroup=haplogroup, detail=isogg, markers_used=used, markers_skipped=skipped,
                     tool=tool)
