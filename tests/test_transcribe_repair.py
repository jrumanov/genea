import pytest
from fixtures.transcripts import loop_rows, speech_rows, whisper

from genea.ingest.transcript import Segment
from genea.transcribe.audio import SAMPLE_RATE
from genea.transcribe.repair import Replacement, RepairError, SpliceError, repair_transcript, splice

DURATION = 160.0


def make(tmp_path, rows, newline="\r\n", audio=True):
    path = tmp_path / "2023_x.txt"
    path.write_bytes(whisper(rows).replace("\n", newline).encode("utf-8"))
    if audio:
        (tmp_path / "2023_x.m4a").write_bytes(b"audio")
    return path


def run(path, script, duration=DURATION, **kwargs):
    """Repair `path`; the fake transcriber returns `script` (times relative to the cut)."""
    calls, said = [], []

    def transcriber(samples, language, on_segment):
        calls.append((len(samples), language))
        for start, end, text in script:
            on_segment(Segment(start, end, text))

    def load(audio, start, end):
        return range(round(((duration if end is None else end) - start) * SAMPLE_RATE))

    report = repair_transcript(path, path.parent / "work", transcriber=transcriber, language="sk", say=said.append,
                               duration_fn=lambda audio: duration, load_fn=load, **kwargs)
    return report, calls, said


TAIL = [(0.0, 5.0, "Nová veta."), (5.0, 110.0, "Dlhá veta.")]  # covers 50 s to the end of the 160 s audio


def lines(path):
    return path.read_bytes().decode("utf-8").split("\r\n")


def test_a_tail_loop_is_replaced_from_the_segment_before_it(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100))
    original = path.read_bytes()
    report, calls, said = run(path, TAIL)
    assert lines(path)[5] == "50.000000\t55.000000\t Nová veta."
    assert lines(path)[6] == "55.000000\t160.000000\t Dlhá veta."
    assert len(lines(path)) == 8 and lines(path)[7] == ""
    assert lines(path)[:5] == original.decode().split("\r\n")[:5]
    assert calls == [(110 * SAMPLE_RATE, "sk")]  # cut from 50 s to the end of the audio
    assert report.spliced and report.backup_created and not report.unreadable
    assert (tmp_path / "2023_x.txt.orig").read_bytes() == original
    assert any(line.startswith("[") and "Nová veta." in line for line in said)  # live progress was printed


def test_a_loop_in_the_middle_keeps_the_text_after_it(tmp_path):
    rows = speech_rows(6) + loop_rows(60, 100) + [(160.0, 170.0, "Potom.")]
    path = make(tmp_path, rows)
    run(path, [(0.0, 60.0, "Nová."), (60.0, 110.0, "Druhá.")], duration=170.0)
    assert lines(path)[-2] == "160.000000\t170.000000\t Potom."
    assert lines(path)[5] == "50.000000\t110.000000\t Nová."


def test_a_transcript_that_stops_early_is_extended(tmp_path):
    path = make(tmp_path, speech_rows(6))
    run(path, [(0.0, 110.0, "Zvyšok nahrávky.")])
    assert lines(path)[4].startswith("40.000000")
    assert lines(path)[5] == "50.000000\t160.000000\t Zvyšok nahrávky."


def test_from_replaces_everything_from_that_second(tmp_path):
    path = make(tmp_path, speech_rows(6))
    run(path, [(0.0, 140.0, "Nové.")], duration=160.0, from_s=30.0)
    assert lines(path)[2] == "20.000000\t160.000000\t Nové."  # one segment before the first one at or after 30 s
    assert lines(path)[1].startswith("10.000000")


def test_second_run_changes_nothing(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100))
    original = path.read_bytes()
    run(path, TAIL)
    done = path.read_bytes()
    report, calls, _ = run(path, TAIL)
    assert report.nothing_to_do and calls == []
    assert path.read_bytes() == done
    assert (tmp_path / "2023_x.txt.orig").read_bytes() == original  # still the first original


def test_an_existing_backup_is_never_overwritten(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100))
    (tmp_path / "2023_x.txt.orig").write_bytes(b"keep me")
    report, _, _ = run(path, TAIL)
    assert (tmp_path / "2023_x.txt.orig").read_bytes() == b"keep me" and not report.backup_created


def test_lf_file_stays_lf(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100), newline="\n")
    run(path, TAIL)
    expected = whisper(speech_rows(5)) + "50.000000\t55.000000\t Nová veta.\n55.000000\t160.000000\t Dlhá veta.\n"
    assert path.read_bytes() == expected.encode("utf-8")


def test_missing_final_newline_is_kept(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100))
    path.write_bytes(path.read_bytes().rstrip(b"\r\n"))
    run(path, TAIL)
    assert path.read_bytes().endswith("Dlhá veta.".encode()) and not path.read_bytes().endswith(b"\n")


def test_lines_after_the_last_segment_are_kept_when_the_span_runs_to_the_end(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100))
    path.write_bytes(path.read_bytes() + "\r\n[koniec nahrávky]\r\n".encode())  # a blank and a stray line
    run(path, TAIL)
    assert lines(path)[5:] == ["50.000000\t55.000000\t Nová veta.", "55.000000\t160.000000\t Dlhá veta.", "",
                               "[koniec nahrávky]", ""]


def test_a_relooping_result_is_not_spliced(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100))
    original = path.read_bytes()
    report, _, _ = run(path, loop_rows(0, 70, "Zase to isté."))
    assert path.read_bytes() == original
    assert report.unreadable and not report.replaced and not report.spliced
    assert report.unreadable[0][1:] == (50.0, 120.0)  # the loop in the new text, on the recording's timeline


def test_empty_output_is_not_spliced(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100))
    original = path.read_bytes()
    report, _, _ = run(path, [])
    assert path.read_bytes() == original and report.unreadable
    assert report.unreadable[0][1:] == (60.0, 160.0)  # the lost span itself, to the end of the audio


def test_empty_output_for_a_middle_loop_reports_the_loop(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100) + [(160.0, 170.0, "Potom.")])
    report, _, _ = run(path, [], duration=170.0)
    assert report.unreadable[0][1:] == (60.0, 160.0)


def test_back_to_back_loops_are_cut_once(tmp_path):
    rows = speech_rows(6) + loop_rows(60, 100, "Prvá slučka.") + loop_rows(160, 100, "Druhá slučka.")
    rows += [(260.0, 270.0, "Potom.")]
    path = make(tmp_path, rows)
    report, calls, _ = run(path, [(0.0, 100.0, "Nová."), (100.0, 210.0, "Druhá nová.")], duration=270.0)
    assert calls == [(210 * SAMPLE_RATE, "sk")]  # one cut from 50 s (the segment before A) to the end of B
    assert report.spliced and not report.unreadable and len(report.replaced) == 1
    span, count = report.replaced[0]
    assert (span.start, span.end, count) == (60.0, 260.0, 2)
    assert lines(path)[5:8] == ["50.000000\t150.000000\t Nová.", "150.000000\t260.000000\t Druhá nová.",
                                "260.000000\t270.000000\t Potom."]


def test_a_failed_replace_leaves_the_transcript_and_no_temp_file(tmp_path, monkeypatch):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100))
    original = path.read_bytes()

    def refuse(src, dst):
        raise PermissionError("the file is open in another program")

    monkeypatch.setattr("genea.transcribe.repair.os.replace", refuse)
    with pytest.raises(RepairError, match="Could not write 2023_x.txt.*open in another program"):
        run(path, TAIL)
    assert path.read_bytes() == original
    assert not (tmp_path / "2023_x.txt.tmp").exists()


def test_unreadable_audio_length_is_an_error_when_nothing_else_is_lost(tmp_path):
    path = make(tmp_path, speech_rows(6))  # it may end early, but that cannot be told without the length
    with pytest.raises(RepairError, match="early end.*uv sync --extra transcribe"):
        run(path, TAIL, duration=None)


def test_unreadable_audio_length_still_repairs_a_loop(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100) + [(160.0, 170.0, "Potom.")])
    report, calls, _ = run(path, [(0.0, 110.0, "Nová.")], duration=None)
    assert report.spliced and len(calls) == 1


def test_no_splice_writes_only_the_work_file(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100))
    original = path.read_bytes()
    report, _, _ = run(path, TAIL, splice_result=False)
    assert path.read_bytes() == original and not report.spliced
    assert not (tmp_path / "2023_x.txt.orig").exists()
    assert report.work_file.read_bytes().decode().splitlines()[0] == "50.000000\t55.000000\t Nová veta."


def test_no_audio_is_an_error(tmp_path):
    path = make(tmp_path, speech_rows(6) + loop_rows(60, 100), audio=False)
    with pytest.raises(RepairError, match="No audio file"):
        run(path, TAIL)


def test_not_a_transcript_is_an_error(tmp_path):
    path = tmp_path / "kronika.txt"
    path.write_text("Kronika obce Brno.\n", encoding="utf-8")
    with pytest.raises(RepairError, match="not a Whisper transcript"):
        run(path, TAIL)


def test_splice_only_touches_the_replaced_lines():
    text = "0.000000\t10.000000\t A\n10.000000\t20.000000\t B\n20.000000\t30.000000\t C"  # no final newline
    result = splice(text, [Replacement(1, 1, [Segment(10.0, 15.0, "x"), Segment(15.0, 20.0, "y")])])
    assert result == ("0.000000\t10.000000\t A\n10.000000\t15.000000\t x\n15.000000\t20.000000\t y\n"
                      "20.000000\t30.000000\t C")


def test_splice_refuses_to_put_timestamps_out_of_order():
    text = whisper([(0, 10, "A"), (10, 20, "B"), (20, 30, "C")])
    with pytest.raises(SpliceError, match="out of order"):
        splice(text, [Replacement(1, 1, [Segment(25.0, 26.0, "x")])])


def test_splice_refuses_overlapping_ranges():
    text = whisper([(0, 10, "A"), (10, 20, "B"), (20, 30, "C")])
    with pytest.raises(SpliceError, match="overlap"):
        splice(text, [Replacement(0, 1, [Segment(0, 1, "x")]), Replacement(1, 2, [Segment(1, 2, "y")])])


def test_splice_checks_the_order_even_when_the_result_no_longer_looks_like_a_transcript():
    text = whisper([(0, 10, "A"), (10, 20, "B"), (20, 30, "C"), (30, 40, "D")]) + "poznámka\n"
    text += whisper([(40, 50, "E")])
    with pytest.raises(SpliceError, match="out of order"):
        splice(text, [Replacement(0, 3, [Segment(100.0, 110.0, "x")])])  # only 2 of 3 lines are segments


def test_splice_leaves_the_lines_before_and_after_byte_identical():
    text = "0.000000\t10.000000\t A\r\npoznámka\n10.000000\t20.000000\t B\r\n20.000000\t30.000000\t C\n"
    result = splice(text, [Replacement(2, 2, [Segment(10.0, 20.0, "x")])])
    assert result == "0.000000\t10.000000\t A\r\npoznámka\n10.000000\t20.000000\t x\r\n20.000000\t30.000000\t C\n"
