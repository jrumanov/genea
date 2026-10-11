"""A small tree with known problems, for the analysis tests.

I5 is born after her mother's death (error) and has no sex; I4 is born 5 months after I3 (needs review);
I6 was born in 1820 with no death; I7 has no events but is named in docs/zapis.txt.
"""

from pathlib import Path

from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.kb import KnowledgeBase, init_kb

TREE = """0 HEAD
1 SOUR GENEA_TEST
1 GEDC
2 VERS 5.5.1
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Karol /Weiss/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Brno
2 SOUR Matrika Brno
1 DEAT
2 DATE 1910
2 SOUR Matrika Brno
1 FAMS @F1@
0 @I2@ INDI
1 NAME Mária /Bielik/
1 SEX F
1 BIRT
2 DATE ABT 1855
1 DEAT
2 DATE 12 MAR 1880
1 FAMS @F1@
1 FAMC @F0@
0 @I3@ INDI
1 NAME Ján /Weiss/
1 SEX M
1 BIRT
2 DATE 1 JAN 1878
2 PLAC Brno
1 FAMC @F1@
0 @I4@ INDI
1 NAME Eva /Weiss/
1 SEX F
1 BIRT
2 DATE 1 JUN 1878
1 FAMC @F1@
0 @I5@ INDI
1 NAME Anna /Weiss/
1 BIRT
2 DATE 2 FEB 1881
1 FAMC @F1@
0 @I6@ INDI
1 NAME Peter /Bielik/
1 SEX M
1 BIRT
2 DATE 1820
1 FAMS @F0@
0 @I7@ INDI
1 NAME Imrich /Novotný/
1 SEX M
0 @F0@ FAM
1 HUSB @I6@
1 CHIL @I2@
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 MARR
2 DATE 1875
1 CHIL @I3@
1 CHIL @I4@
1 CHIL @I5@
0 TRLR
"""

DOCS = {"docs/zapis.txt": "Imrich Novotný bol krstným otcom v Brne.\n"}


def make_analysis_kb(root: Path, tree: str = TREE, *, encoding: str = "utf-8", newline: str = "\n",
                     bom: bytes = b"", docs: dict[str, str] = DOCS) -> KnowledgeBase:
    root.mkdir(parents=True, exist_ok=True)
    (root / "tree.ged").write_bytes(bom + tree.replace("\n", newline).encode(encoding))
    for rel, text in docs.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    kb = init_kb(root)
    index_kb(kb, FakeEmbedder())
    return kb


def event_line(ctx, xref: str, type_: str) -> int:
    """GEDCOM line of the person's first event of this type, as the index knows it."""
    row = ctx.conn.execute("SELECT line_no FROM events WHERE ged_file = 'tree.ged' AND owner_xref = ? AND type = ? "
                           "ORDER BY line_no", (xref, type_)).fetchone()
    return row["line_no"]
