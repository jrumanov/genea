from genea.analysis.sourcenote import same_note, with_source
from genea.tools.models import SourceRef


def test_with_source_appends_cites():
    refs = [SourceRef(file="docs/a.pdf", page=3), SourceRef(file="docs/b.txt")]
    assert with_source("Mlynár  \n", refs) == "Mlynár\n\nSource: [docs/a.pdf p.3]; [docs/b.txt]"


def test_same_note_ignores_spacing():
    assert same_note("Mlynár v  Brne\n\nSource: [x]", " Mlynár v Brne Source: [x]\n")
    assert not same_note("Mlynár", "Mlynár v Brne")
