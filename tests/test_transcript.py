import pytest
from fixtures.transcripts import LOOP_TEXT, loop_rows, speech_rows, whisper

from genea.ingest.docs import chunk_segments, read_raw_transcript
from genea.ingest.transcript import (
    DeadSpan,
    Run,
    Segment,
    collapse_loops,
    dead_spans,
    find_runs,
    format_time,
    is_transcript,
    parse_line,
    parse_segments,
    parse_transcript,
    transcript_header,
)


def test_detects_whisper_output():
    assert is_transcript(whisper([(0.6, 8.56, "Dobre, takže podcast."), (8.56, 10.56, "Čo mám hovoriť?")]))


@pytest.mark.parametrize("text", ["", "   \n\n", "Kronika obce Brno.\n\nJosef Novák, sedlák.\n",
                                  "1845\tBrno\n1846\tPraha\n"])
def test_ordinary_text_is_not_a_transcript(text):
    assert not is_transcript(text)


def test_year_page_table_is_not_a_transcript():
    table = "1845\t12\tNarodenie: Ján Novák\n1846\t13\tÚmrtie: Anna Nováková\n1847\t14\tSobáš: Jozef\n"
    assert not is_transcript(table)
    assert parse_transcript(table) == []


def test_line_ending_before_it_starts_does_not_count():
    assert not is_transcript("9.000000\t2.000000\t text\n8.000000\t1.000000\t more\n")


def test_share_of_matching_lines_decides():
    rows = whisper([(i, i + 1, "x") for i in range(4)])
    assert is_transcript(rows + "a stray line\n")  # 4 of 5 lines
    assert not is_transcript(rows + "a stray line\nand another\n")  # 4 of 6 lines


def test_parse_strips_text_and_skips_blank_and_stray_lines():
    text = whisper([(0.6, 8.56, "Dobre."), (8.56, 9.0, "   "), (9.0, 12.5, "Čo mám hovoriť?"),
                    (12.5, 14.0, "Áno."), (14.0, 15.0, "Nie.")]) + "stray\n"  # 5 of 6 lines still count
    assert parse_transcript(text) == [Segment(0.6, 8.56, "Dobre."), Segment(9.0, 12.5, "Čo mám hovoriť?"),
                                      Segment(12.5, 14.0, "Áno."), Segment(14.0, 15.0, "Nie.")]


def test_parse_of_ordinary_text_is_empty():
    assert parse_transcript("Kronika obce Brno.\n") == []


def test_windows_line_endings():
    text = whisper([(0, 2, "Dobre."), (2, 4, "Čo mám hovoriť?")]).replace("\n", "\r\n")
    assert [s.text for s in parse_transcript(text)] == ["Dobre.", "Čo mám hovoriť?"]


def test_whisper_loops_collapse_to_one_segment():
    text = whisper([(0, 2, "Dobre."), (2, 4, "Ďakujem."), (4, 6, "Ďakujem."), (6, 8, "Ďakujem."),
                    (8, 10, "Ďakujem."), (10, 12, "Hotovo.")])
    assert parse_transcript(text) == [Segment(0, 2, "Dobre."), Segment(2, 10, "Ďakujem."), Segment(10, 12, "Hotovo.")]


def test_two_repeats_are_kept():
    assert len(parse_transcript(whisper([(0, 2, "Áno."), (2, 4, "Áno."), (4, 6, "Nie.")]))) == 3


@pytest.mark.parametrize(("seconds", "shown"), [(0, "0:00"), (9.9, "0:09"), (840, "14:00"), (3599, "59:59"),
                                                (3725, "1:02:05")])
def test_format_time(seconds, shown):
    assert format_time(seconds) == shown


def test_header_from_file_name():
    assert transcript_header("2022_05_10-fabik_lubica_rozhovor_1") == "Nahrávka 2022-05-10 · fabik lubica rozhovor 1"
    assert transcript_header("rozhovor_s_babkou") == "Nahrávka · rozhovor s babkou"


def _segments(count, text_len=100, gaps=None):
    """`count` two-second segments of `text_len` characters; gaps={i: seconds of silence before segment i}."""
    gaps = gaps or {}
    out, clock = [], 0.0
    for i in range(count):
        clock += gaps.get(i, 0.0)
        out.append(Segment(clock, clock + 2.0, f"{i:03d}" + "x" * (text_len - 3)))
        clock += 2.0
    return out


def test_chunks_carry_times_and_clean_text():
    segments = _segments(30)
    chunks = chunk_segments(segments, size=1000, overlap_segments=2)
    assert [c.chunk_no for c in chunks] == list(range(len(chunks)))
    assert chunks[0].start_s == segments[0].start
    assert chunks[-1].end_s == segments[-1].end
    assert all(c.page is None and c.kind == "text" for c in chunks)
    assert "\t" not in chunks[0].text and chunks[0].text.startswith("000xxx")


def test_chunk_prefers_a_long_pause_near_the_size_limit():
    segments = _segments(30, gaps={8: 5.0})  # 5 s of silence before segment 8
    chunks = chunk_segments(segments, size=1000, overlap_segments=2)
    assert chunks[0].end_s == segments[7].end
    assert chunks[1].start_s == segments[6].start  # two segments of overlap


def test_chunk_without_a_pause_closes_at_the_size_limit():
    segments = _segments(30)
    chunks = chunk_segments(segments, size=1000, overlap_segments=0)
    assert chunks[0].end_s == segments[9].end  # ten 100-character segments reach 1000
    assert chunks[1].start_s == segments[10].start


def test_every_segment_is_in_some_chunk():
    segments = _segments(55)
    joined = " ".join(c.text for c in chunk_segments(segments, size=1000))
    assert all(s.text in joined for s in segments)


def test_a_segment_longer_than_the_chunk_size_still_terminates():
    segments = [Segment(0, 5, "y" * 5000), Segment(5, 6, "short")]
    chunks = chunk_segments(segments, size=1000)
    assert [c.text for c in chunks] == ["y" * 5000, "short"]


def test_no_segments_no_chunks():
    assert chunk_segments([]) == []


def test_parse_line_returns_a_segment_or_none():
    assert parse_line("0.600000\t8.560000\t Dobre.\r\n") == Segment(0.6, 8.56, "Dobre.")
    assert parse_line("stray line") is None
    assert parse_line("1.000000\t2.000000\t   \n") is None


def test_parse_segments_keeps_loops_that_parse_transcript_collapses():
    text = whisper([(0, 2, "A."), (2, 4, "B."), (4, 6, "B."), (6, 8, "B."), (8, 10, "C.")])
    assert len(parse_segments(text)) == 5
    assert len(parse_transcript(text)) == 3
    assert parse_segments("Kronika obce Brno.\n") == []


def test_find_runs_needs_three_in_a_row():
    segs = [Segment(0, 1, "a"), Segment(1, 2, "b"), Segment(2, 3, "b"),
            Segment(3, 4, "c"), Segment(4, 5, "c"), Segment(5, 6, "c")]
    assert find_runs(segs) == [Run(3, 5, "c")]


def test_a_long_loop_is_a_dead_span():
    rows = speech_rows(2) + loop_rows(20, 100)
    assert dead_spans(parse_segments(whisper(rows))) == [DeadSpan(20.0, 120.0, LOOP_TEXT, 100, 2, 101)]


def test_a_short_loop_is_not_a_dead_span():
    rows = speech_rows(1) + loop_rows(10, 30)  # 30 s of the same line is a stutter
    assert dead_spans(parse_segments(whisper(rows))) == []


def test_a_loop_of_exactly_sixty_seconds_is_a_dead_span():
    exact = [Segment(0.0, 1.0, "a")] + [Segment(1.0 + i * 20, 1.0 + (i + 1) * 20, "b") for i in range(3)]
    assert dead_spans(exact) == [DeadSpan(1.0, 61.0, "b", 3, 1, 3)]
    short = [Segment(0.0, 1.0, "a")] + [Segment(1.0 + i * 19.95, 1.0 + (i + 1) * 19.95, "b") for i in range(3)]
    assert dead_spans(short) == []  # 59.85 s


def test_collapse_loops_merges_each_run_and_keeps_the_rest():
    segs = [Segment(0, 1, "a"), Segment(1, 2, "b"), Segment(2, 3, "b"), Segment(3, 4, "b"),
            Segment(4, 5, "c"), Segment(5, 6, "c"), Segment(6, 7, "d")]
    assert collapse_loops(segs) == [Segment(0, 1, "a"), Segment(1, 4, "b"), Segment(4, 5, "c"),
                                    Segment(5, 6, "c"), Segment(6, 7, "d")]
    assert collapse_loops([]) == []


def test_read_raw_transcript_keeps_loops_and_ignores_other_files(tmp_path):
    rows = speech_rows(2) + loop_rows(20, 5)
    transcript = tmp_path / "x.txt"
    transcript.write_bytes(("﻿" + whisper(rows).replace("\n", "\r\n")).encode("utf-8"))  # BOM and CRLF
    raw = read_raw_transcript(transcript)
    assert raw == parse_segments(whisper(rows)) and len(raw) == 7  # the loop is not collapsed
    notes = tmp_path / "notes.txt"
    notes.write_text("Kronika obce Brno.\n", encoding="utf-8")
    assert read_raw_transcript(notes) == []
    other = tmp_path / "x.md"
    other.write_text(whisper(rows), encoding="utf-8")
    assert read_raw_transcript(other) == []  # only .txt files are transcripts
