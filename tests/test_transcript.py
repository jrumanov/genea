import pytest
from fixtures.transcripts import whisper

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
