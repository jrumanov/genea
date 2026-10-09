import pytest
from fixtures.transcripts import whisper

from genea.ingest.docs import chunk_segments
from genea.ingest.transcript import Segment, format_time, is_transcript, parse_transcript, transcript_header


def test_detects_whisper_output():
    assert is_transcript(whisper([(0.6, 8.56, "Dobre, takže podcast."), (8.56, 10.56, "Čo mám hovoriť?")]))


@pytest.mark.parametrize("text", ["", "   \n\n", "Kronika obce Brno.\n\nJosef Novák, sedlák.\n",
                                  "1845\tBrno\n1846\tPraha\n"])
def test_ordinary_text_is_not_a_transcript(text):
    assert not is_transcript(text)


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
