"""Real Haplogrep and yhaplo runs on public or synthetic data (never the user's file).

Run:  uv sync --extra dna && uv run genea dna setup && uv run pytest -m live tests/live/test_live_dna.py
"""

import pytest
from fixtures.dna import Y_R1B

from genea.dna import haplo, runtime

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def real_tools_dir(monkeypatch):
    """conftest points GENEA_HOME at an empty folder; the live tests need the tools `genea dna setup` installed."""
    monkeypatch.delenv("GENEA_HOME", raising=False)


def test_mtdna_reproduces_haplogreps_own_example(tmp_path):
    if runtime.find_java().path is None or not runtime.rcrs_fasta().is_file():
        pytest.skip("run `genea dna setup` first")
    example = runtime.haplogrep_dir() / "data" / "examples" / "example-microarray.vcf"
    rows, column = [], None
    for line in example.read_text(encoding="utf-8").splitlines():
        if line.startswith("##"):
            continue
        cols = line.split("\t")
        if line.startswith("#"):
            column = cols.index("NA12282")
            continue
        if cols[column] != ".":
            base = cols[4] if cols[column] == "1" else cols[3]
            rows.append((f"i{cols[1]}", "MT", int(cols[1]), base * 2))
    call = haplo.call_mtdna(rows, tmp_path)
    assert call.haplogroup == "J1b1a", call


def test_y_r1b(tmp_path):
    if runtime.yhaplo_version() is None:
        pytest.skip("uv sync --extra dna")
    rows = [(f"rs{i}", "Y", pos, base * 2) for i, (pos, base) in enumerate(Y_R1B)]
    call = haplo.call_y("male", rows, tmp_path)
    assert (call.haplogroup, call.detail) == ("R-P312", "R1b1a2a1a2"), call
