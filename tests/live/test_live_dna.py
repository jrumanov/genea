"""Real Haplogrep and yhaplo runs on public or synthetic data (never the user's file).

Run:  uv sync --extra dna && uv run genea dna setup && uv run pytest -m live tests/live/test_live_dna.py
"""

import os

import pytest
from fixtures.dna import Y_R1B

from genea.dna import haplo, runtime

pytestmark = pytest.mark.live

# Captured at import (collection), before conftest's autouse fixture points GENEA_HOME at an empty folder.
REAL_GENEA_HOME = os.environ.get("GENEA_HOME")


@pytest.fixture(autouse=True)
def real_tools_dir(monkeypatch):
    """conftest points GENEA_HOME at an empty folder; the live tests need the tools `genea dna setup` installed."""
    if REAL_GENEA_HOME is None:
        monkeypatch.delenv("GENEA_HOME", raising=False)
    else:
        monkeypatch.setenv("GENEA_HOME", REAL_GENEA_HOME)


def test_mtdna_reproduces_haplogreps_own_example(tmp_path):
    if runtime.find_java().path is None or not runtime.rcrs_fasta().is_file():
        pytest.skip("run `genea dna setup` first")
    example = runtime.haplogrep_dir() / "data" / "examples" / "example-microarray.vcf"
    if not example.is_file():
        pytest.skip(f"Haplogrep example VCF not found at {example}; run `genea dna setup`")
    rows, column = [], None
    for line in example.read_text(encoding="utf-8").splitlines():
        if line.startswith("##"):
            continue
        cols = line.split("\t")
        if line.startswith("#"):
            column = cols.index("NA12282")
            continue
        gt = cols[column].split(":")[0]
        allele = gt.replace("|", "/").split("/")[0]
        if allele != ".":
            base = cols[3] if allele == "0" else cols[4].split(",")[int(allele) - 1]
            rows.append((f"i{cols[1]}", "MT", int(cols[1]), base * 2))
    call = haplo.call_mtdna(rows, tmp_path)
    assert call.haplogroup == "J1b1a", call


def test_y_r1b(tmp_path):
    if runtime.yhaplo_version() is None:
        pytest.skip("uv sync --extra dna")
    rows = [(f"rs{i}", "Y", pos, base * 2) for i, (pos, base) in enumerate(Y_R1B)]
    call = haplo.call_y("male", rows, tmp_path)
    assert (call.haplogroup, call.detail) == ("R-P312", "R1b1a2a1a2"), call
