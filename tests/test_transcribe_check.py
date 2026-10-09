from fixtures.transcripts import LOOP_TEXT, loop_rows, speech_rows, whisper

from genea.config import Settings
from genea.ingest.docs import read_raw_transcript
from genea.ingest.transcript import parse_segments
from genea.kb import KnowledgeBase
from genea.transcribe.check import LostSpan, check_file, find_lost_spans, loop_summary, transcript_paths, transcripts


def segs(rows):
    return parse_segments(whisper(rows))


def test_a_loop_to_the_end_is_a_dead_span_up_to_the_end_of_the_audio():
    spans = find_lost_spans(segs(speech_rows(6) + loop_rows(60, 100)), audio_seconds=160.0)
    assert spans == [LostSpan("dead", 60.0, None, LOOP_TEXT, 6, 105)]


def test_a_loop_in_the_middle_keeps_its_end():
    rows = speech_rows(6) + loop_rows(60, 100) + [(160.0, 170.0, "Potom.")]
    assert find_lost_spans(segs(rows), audio_seconds=170.0) == [LostSpan("dead", 60.0, 160.0, LOOP_TEXT, 6, 105)]


def test_a_transcript_that_stops_early_is_an_early_end():
    assert find_lost_spans(segs(speech_rows(6)), audio_seconds=200.0) == [LostSpan("early_end", 60.0, None, "", 5, 5)]


def test_early_end_needs_the_audio_length():
    assert find_lost_spans(segs(speech_rows(6)), audio_seconds=None) == []


def test_complete_transcript_has_no_lost_spans():
    assert find_lost_spans(segs(speech_rows(6)), audio_seconds=62.0) == []  # 2 s of silence at the end is fine
    short_loop = speech_rows(1) + loop_rows(10, 30) + [(40.0, 50.0, "Hotovo.")]
    assert find_lost_spans(segs(short_loop), audio_seconds=50.0) == []


def test_check_file_reports_where_real_text_ends(tmp_path):
    path = tmp_path / "x.txt"
    path.write_text(whisper(speech_rows(6) + loop_rows(60, 100)), encoding="utf-8")
    (tmp_path / "x.m4a").write_bytes(b"audio")
    result = check_file(path, duration_fn=lambda audio: 160.0)
    assert result.speech_end == 60.0 and result.audio_seconds == 160.0 and len(result.spans) == 1


def test_check_file_without_audio_still_finds_dead_spans(tmp_path):
    path = tmp_path / "x.txt"
    path.write_text(whisper(speech_rows(6) + loop_rows(60, 100)), encoding="utf-8")
    result = check_file(path)
    assert result.audio_seconds is None and len(result.spans) == 1


def test_check_file_ignores_ordinary_text(tmp_path):
    path = tmp_path / "kronika.txt"
    path.write_text("Kronika obce Brno.\n", encoding="utf-8")
    assert check_file(path) is None


def test_transcript_paths_and_summary(tmp_path):
    root = tmp_path.resolve()
    (root / "docs").mkdir()
    (root / "docs" / "good.txt").write_text(whisper(speech_rows(6)), encoding="utf-8")
    (root / "docs" / "bad.txt").write_text(whisper(speech_rows(6) + loop_rows(60, 100)), encoding="utf-8")
    (root / "docs" / "kronika.txt").write_text("Kronika obce Brno.\n", encoding="utf-8")
    kb = KnowledgeBase(root, Settings())
    assert [p.name for p in transcript_paths(kb)] == ["bad.txt", "good.txt"]
    assert "1 of 2" in loop_summary(kb)


def test_each_transcript_is_read_once(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    (root / "good.txt").write_text(whisper(speech_rows(6)), encoding="utf-8")
    (root / "bad.txt").write_text(whisper(speech_rows(6) + loop_rows(60, 100)), encoding="utf-8")
    reads = []

    def counting(path):
        reads.append(path.name)
        return read_raw_transcript(path)

    monkeypatch.setattr("genea.transcribe.check.read_raw_transcript", counting)
    kb = KnowledgeBase(root, Settings())
    assert "1 of 2" in loop_summary(kb)
    assert sorted(reads) == ["bad.txt", "good.txt"]
    reads.clear()
    assert [(p.name, len(segments)) for p, segments in transcripts(kb)] == [("bad.txt", 106), ("good.txt", 6)]
    assert sorted(reads) == ["bad.txt", "good.txt"]
