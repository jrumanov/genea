import pytest
from fixtures.transcripts import NAME, sample_rows, write_transcript

from genea.index.embed import FakeEmbedder
from genea.index.indexer import index_kb
from genea.ingest.summarize import FakeSummarizer
from genea.tool_runner import TRUNCATION_MARKER, render
from genea.tools.base import ToolError, open_tools
from genea.tools.mentions import documents_about


def _reindex(tools):
    index_kb(tools.kb, FakeEmbedder())


def test_lists_every_linked_document_grouped_by_file(tools):
    r = documents_about(tools, "tree.ged#@I3@")
    files = [f.file for f in r.files]
    assert "docs/kronika.txt" in files and "docs/matrika.pdf" in files
    assert len(files) == len(set(files)) == r.total_files
    assert r.sources and all(f.mentions for f in r.files)
    assert [s.file for s in r.sources] == files
    assert r.person.id == "tree.ged#@I3@" and "find_images" in r.note


def test_same_name_people_are_ambiguous_but_relatives_are_not(tools):
    jan = documents_about(tools, "tree.ged#@I3@")
    assert all(f.ambiguous for f in jan.files) and jan.ambiguous_files == jan.total_files
    assert "same name" in jan.note
    # the chronicle also names Josef and Jan Novák, but Anna Svobodová has no namesake
    anna = documents_about(tools, "tree.ged#@I2@")
    assert not next(f for f in anna.files if f.file == "docs/kronika.txt").ambiguous


def test_a_shared_initial_is_ambiguous_only_when_written(tools):
    # Jiří and Jan Novák share the form "j novak"; it is only ambiguous where the text says "J. Novák"
    docs = tools.kb.root / "docs"
    (docs / "rodina.txt").write_text("Jiří Novák a jeho otec Jan Novák žili v Brně.", encoding="utf-8")
    (docs / "zapis.txt").write_text("Jiří Novák podpísal zápis, svedok J. Novák.", encoding="utf-8")
    _reindex(tools)
    by_file = {f.file: f for f in documents_about(tools, "tree.ged#@I17@").files}
    assert not by_file["docs/rodina.txt"].ambiguous
    assert by_file["docs/zapis.txt"].ambiguous


def test_declined_mention_is_a_variant(tools):
    r = documents_about(tools, "tree.ged#@I2@")
    kronika = next(f for f in r.files if f.file == "docs/kronika.txt")
    assert kronika.match == "variant" and "Annou Svobodovou" in kronika.mentions[0].snippet


def test_files_are_ordered_by_confidence(tools):
    r = documents_about(tools, "tree.ged#@I3@")
    keys = [(-f.confidence, -f.chunks, f.file) for f in r.files]
    assert keys == sorted(keys)


def test_person_without_documents(tools):
    r = documents_about(tools, "tree.ged#@I20@")
    assert (r.total_files, r.pages, r.files) == (0, 1, [])
    assert r.page == 1 and r.total_chunks == 0 and r.ambiguous_files == 0


def test_person_without_a_name(tools):
    ged = tools.kb.root / "tree.ged"
    ged.write_text(ged.read_text(encoding="utf-8").replace("0 TRLR", "0 @I21@ INDI\n1 SEX M\n0 TRLR"),
                   encoding="utf-8")
    _reindex(tools)
    r = documents_about(tools, "tree.ged#@I21@")
    assert (r.total_files, r.total_chunks, r.pages, r.files, r.sources) == (0, 0, 1, [], [])


def test_unknown_person_is_a_tool_error(tools):
    with pytest.raises(ToolError, match="find_person"):
        documents_about(tools, "tree.ged#@I99@")


FILLER = "Slovo o rodine a o dome na dedine. " * 46  # about 1,600 characters


def test_pages_cover_everything_and_fit_the_limit(tools):
    # write 15 long files that mention Terezie Dvořáková, re-index, then page through
    for number in range(15):
        text = (f"{FILLER}Terezie Dvořáková žila v Tišnově, zápis {number}. {FILLER}{FILLER}"
                f"Terezie Dvořáková zomrela v Tišnově. {FILLER}")
        assert len(text) > 3000
        (tools.kb.root / "docs" / f"dlhy_{number:02d}.txt").write_text(text, encoding="utf-8")
    _reindex(tools)
    first = documents_about(tools, "tree.ged#@I6@")
    seen = [f.file for p in range(1, first.pages + 1) for f in documents_about(tools, "tree.ged#@I6@", p).files]
    assert len(seen) == len(set(seen)) == first.total_files >= 16
    assert first.pages == 3 and len(first.files) == 6
    assert any(f.chunks >= 2 and len(f.mentions) >= 2 for f in first.files)
    for p in range(1, first.pages + 1):
        out = render(tools, documents_about(tools, "tree.ged#@I6@", p))
        assert len(out) <= tools.kb.settings.tool_output_chars and TRUNCATION_MARKER not in out
    with pytest.raises(ToolError, match="between 1 and"):
        documents_about(tools, "tree.ged#@I6@", first.pages + 1)
    with pytest.raises(ToolError, match="between 1 and 3"):
        documents_about(tools, "tree.ged#@I6@", 0)


JOSEF_ROW = (0.0, 5.0, "Josef Novák odišiel v roku 1700 do sveta.")


def test_summaries_are_never_returned(tools):
    # the fake summary repeats "Josef Novák", so only the transcript's own text may be returned
    write_transcript(tools.kb.root, rows=[JOSEF_ROW, *sample_rows()[1:]])
    index_kb(tools.kb, FakeEmbedder(), summarizer=FakeSummarizer())
    summaries = {(r["path"], r["chunk_no"]) for r in tools.conn.execute(
        "SELECT f.path, c.chunk_no FROM chunks c JOIN files f ON f.id = c.file_id WHERE c.kind = 'summary'")}
    assert summaries
    r = documents_about(tools, "tree.ged#@I1@")
    transcript = next(f for f in r.files if f.file == f"docs/{NAME}")
    assert transcript.mentions[0].start_s == 0.0 and "Josef Novák" in transcript.mentions[0].snippet
    assert not {(f.file, m.chunk) for f in r.files for m in f.mentions} & summaries
