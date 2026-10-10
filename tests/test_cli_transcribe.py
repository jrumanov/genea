import importlib.util
import os
import re
import struct
import subprocess
import sys
import wave

import pytest
from fixtures.transcripts import loop_rows, speech_rows, whisper
from typer.testing import CliRunner

from genea.cli.main import app
from genea.ingest.transcript import Segment
from genea.transcribe import audio as real_audio

runner = CliRunner()
HAVE_AUDIO = importlib.util.find_spec("av") is not None and importlib.util.find_spec("numpy") is not None


def write_silence(path, seconds):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<h", 0) * (16000 * int(seconds)))


class FakeSamples:
    """Stands in for the samples of a span: only its length is used."""

    def __init__(self, count):
        self.count = count

    def __len__(self):
        return self.count


def wave_seconds(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


@pytest.fixture(autouse=True)
def audio_backend(monkeypatch):
    """Without the transcribe extra (no av / numpy), read the test .wav files with the standard library."""
    if HAVE_AUDIO:
        return

    def duration(path):
        return wave_seconds(path)

    def load_span(path, start=0.0, end=None):
        stop = wave_seconds(path) if end is None else min(end, wave_seconds(path))
        return FakeSamples(int(max(stop - start, 0.0) * real_audio.SAMPLE_RATE))

    monkeypatch.setattr("genea.cli.transcribecmds.audio_duration", duration)
    monkeypatch.setattr("genea.cli.transcribecmds.load_span", load_span)


@pytest.fixture
def kb_with_loop(tmp_path):
    root = tmp_path / "Foto DB"  # a space in the folder name, like D:\Foto DB
    (root / "podcast").mkdir(parents=True)
    transcript = root / "podcast" / "2023_x-fabik.txt"
    transcript.write_text(whisper(speech_rows(6) + loop_rows(60, 100, "No a ja tam si sa učila.")), encoding="utf-8")
    write_silence(root / "podcast" / "2023_x-fabik.wav", 160)
    return root, transcript


class FakeTranscriber:
    def __init__(self, script):
        self.script = script
        self.calls = []

    def __call__(self, samples, language, on_segment):
        self.calls.append((len(samples), language))
        for start, end, text in self.script:
            on_segment(Segment(start, end, text))


def patch_transcriber(monkeypatch, script):
    fake = FakeTranscriber(script)
    monkeypatch.setattr("genea.cli.transcribecmds.make_transcriber", lambda settings, model: fake)
    return fake


def test_check_in_a_folder_with_a_space(kb_with_loop):
    root, _ = kb_with_loop
    result = runner.invoke(app, ["--kb", str(root), "transcripts", "check"])
    assert result.exit_code == 1, result.output
    assert "podcast/2023_x-fabik.txt" in result.output
    assert "dead span 1:00–end (100×" in result.output
    assert "real text: 0:00–1:00 of 2:40" in result.output


def test_check_works_without_the_transcribe_extra(kb_with_loop, monkeypatch):
    root, _ = kb_with_loop
    monkeypatch.setattr("genea.cli.transcribecmds.audio_duration", real_audio.audio_duration)
    monkeypatch.setitem(sys.modules, "av", None)  # makes `import av` fail like a missing extra
    result = runner.invoke(app, ["--kb", str(root), "transcripts", "check"])
    assert result.exit_code == 1, result.output
    assert "dead span 1:00–end (100×" in result.output
    assert "real text: 0:00–1:00\n" in result.output
    assert "transcribe extra is missing" in result.output


def test_check_of_a_clean_kb_exits_zero(tmp_path):
    (tmp_path / "good.txt").write_text(whisper(speech_rows(6)), encoding="utf-8")
    result = runner.invoke(app, ["--kb", str(tmp_path), "transcripts", "check"])
    assert result.exit_code == 0 and "No lost spans found." in result.output


def test_check_survives_cp1252_stdout(kb_with_loop, tmp_path):
    root, _ = kb_with_loop
    env = {**os.environ, "GENEA_HOME": str(tmp_path / "home"), "PYTHONIOENCODING": "cp1252"}
    env.pop("PYTHONUTF8", None)
    proc = subprocess.run([sys.executable, "-c", "from genea.cli.main import app; app()", "--kb", str(root),
                           "transcripts", "check"], env=env, capture_output=True, timeout=120)
    assert b"Traceback" not in proc.stderr, proc.stderr.decode("utf-8", "replace")
    assert "učila".encode() in proc.stdout  # the Slovak text is printed in UTF-8


def test_repair_splices_and_prints_live_progress(kb_with_loop, monkeypatch):
    root, transcript = kb_with_loop
    fake = patch_transcriber(monkeypatch, [(0.0, 5.0, "Nová veta."), (5.0, 110.0, "Dlhá veta.")])
    result = runner.invoke(app, ["--kb", str(root), "transcripts", "repair", str(transcript), "--language", "sk"])
    assert result.exit_code == 0, result.output
    assert re.search(r"\[\s*\d+\.\d%\] \d+:\d\d  elapsed [\d.]+m  eta (\?|[\d.]+m)  Nová veta\.", result.output)
    assert "Language: sk" in result.output and "Run `genea index`" in result.output
    assert fake.calls and fake.calls[0][1] == "sk"
    assert "Dlhá veta." in transcript.read_text(encoding="utf-8")
    assert transcript.with_name(transcript.name + ".orig").is_file()


def test_repair_with_auto_language_says_so(kb_with_loop, monkeypatch):
    root, transcript = kb_with_loop
    patch_transcriber(monkeypatch, [(0.0, 110.0, "x")])
    result = runner.invoke(app, ["--kb", str(root), "transcripts", "repair", str(transcript)])
    assert "Language: auto-detect" in result.output and "transcribe_language" in result.output


def test_repair_that_loops_again_exits_one_and_keeps_the_file(kb_with_loop, monkeypatch):
    root, transcript = kb_with_loop
    before = transcript.read_bytes()
    patch_transcriber(monkeypatch, loop_rows(0, 70, "Zase to isté."))
    result = runner.invoke(app, ["--kb", str(root), "transcripts", "repair", str(transcript)])
    assert result.exit_code == 1 and "still unreadable at 0:50–2:00" in result.output
    assert transcript.read_bytes() == before


def test_repair_without_audio_explains(kb_with_loop, monkeypatch):
    root, transcript = kb_with_loop
    transcript.with_suffix(".wav").unlink()
    result = runner.invoke(app, ["--kb", str(root), "transcripts", "repair", str(transcript)])
    assert result.exit_code == 1 and "No audio file" in result.output


def test_transcribe_writes_a_whisper_file_and_refuses_to_overwrite(tmp_path, monkeypatch):
    audio = tmp_path / "2023_y.wav"
    write_silence(audio, 30)
    patch_transcriber(monkeypatch, [(0.0, 10.0, "Dobrý deň."), (10.0, 25.0, "To sú kokosové.")])
    result = runner.invoke(app, ["transcribe", str(audio), "--start", "5", "--language", "sk"])
    assert result.exit_code == 0, result.output
    out = (tmp_path / "2023_y.txt").read_bytes().decode("utf-8")
    assert out == "5.000000\t15.000000\t Dobrý deň.\r\n15.000000\t30.000000\t To sú kokosové.\r\n"
    assert "2 segments written to" in result.output
    again = runner.invoke(app, ["transcribe", str(audio)])
    assert again.exit_code == 1 and "already exists" in again.output


def test_transcribe_into_a_missing_folder_says_so(tmp_path, monkeypatch):
    audio = tmp_path / "2023_y.wav"
    write_silence(audio, 30)
    patch_transcriber(monkeypatch, [(0.0, 10.0, "Dobrý deň.")])
    result = runner.invoke(app, ["transcribe", str(audio), "--out", str(tmp_path / "nie" / "x.txt")])
    assert result.exit_code == 1 and isinstance(result.exception, SystemExit), result.output
    assert "does not exist" in result.output and not (tmp_path / "nie").exists()


def test_a_failed_transcribe_leaves_no_file_and_can_be_run_again(tmp_path, monkeypatch):
    audio = tmp_path / "2023_y.wav"
    write_silence(audio, 30)

    def broken(samples, language, on_segment):
        on_segment(Segment(0.0, 10.0, "Dobrý deň."))
        raise RuntimeError("whisper.cpp crashed")

    monkeypatch.setattr("genea.cli.transcribecmds.make_transcriber", lambda settings, model: broken)
    result = runner.invoke(app, ["transcribe", str(audio)])
    assert isinstance(result.exception, RuntimeError)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2023_y.wav"]  # no .txt and no .txt.part
    patch_transcriber(monkeypatch, [(0.0, 10.0, "Dobrý deň.")])
    again = runner.invoke(app, ["transcribe", str(audio)])
    assert again.exit_code == 0, again.output
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2023_y.txt", "2023_y.wav"]


def test_an_interrupted_transcribe_keeps_the_part_aside_and_can_be_run_again(tmp_path, monkeypatch):
    audio = tmp_path / "2023_y.wav"
    write_silence(audio, 30)

    def interrupted(samples, language, on_segment):
        on_segment(Segment(0.0, 10.0, "Dobrý deň."))
        raise KeyboardInterrupt

    monkeypatch.setattr("genea.cli.transcribecmds.make_transcriber", lambda settings, model: interrupted)
    result = runner.invoke(app, ["transcribe", str(audio)])
    assert result.exit_code == 130 and "2023_y.txt.part" in result.output
    assert not (tmp_path / "2023_y.txt").exists()
    assert (tmp_path / "2023_y.txt.part").read_bytes() == "0.000000\t10.000000\t Dobrý deň.\r\n".encode()
    patch_transcriber(monkeypatch, [(0.0, 10.0, "Celé.")])
    again = runner.invoke(app, ["transcribe", str(audio)])
    assert again.exit_code == 0, again.output
    assert sorted(p.name for p in tmp_path.iterdir()) == ["2023_y.txt", "2023_y.wav"]


def test_repair_without_a_readable_audio_length_does_not_say_nothing_to_repair(kb_with_loop, monkeypatch):
    root, transcript = kb_with_loop
    transcript.write_text(whisper(speech_rows(6)), encoding="utf-8")  # ends at 1:00 of a 2:40 recording
    monkeypatch.setattr("genea.cli.transcribecmds.audio_duration", real_audio.audio_duration)
    monkeypatch.setitem(sys.modules, "av", None)  # makes `import av` fail like a missing extra
    result = runner.invoke(app, ["--kb", str(root), "transcripts", "repair", str(transcript)])
    assert result.exit_code == 1, result.output
    assert "Nothing to repair" not in result.output
    assert "uv sync --extra transcribe" in result.output


def test_check_of_a_file_that_is_not_a_transcript_says_so(tmp_path):
    notes = tmp_path / "kronika.txt"
    notes.write_text("Kronika obce Brno.\n", encoding="utf-8")
    result = runner.invoke(app, ["--kb", str(tmp_path), "transcripts", "check", str(notes)])
    assert result.exit_code == 2, result.output
    assert "not a Whisper transcript" in result.output and "No lost spans found." not in result.output


def test_doctor_survives_an_unreadable_transcript(kb_with_loop, monkeypatch):
    root, _ = kb_with_loop
    monkeypatch.setattr("genea.cli.kbcmds.run_checks", lambda settings: [])

    def broken(kb):
        raise PermissionError("podcast/2023_x-fabik.txt is locked")

    monkeypatch.setattr("genea.cli.kbcmds.loop_summary", broken)
    result = runner.invoke(app, ["--kb", str(root), "doctor"])
    assert result.exit_code == 0, result.output
    assert "transcripts not checked: podcast/2023_x-fabik.txt is locked" in result.output


def test_doctor_mentions_lost_spans(kb_with_loop, monkeypatch):
    root, _ = kb_with_loop
    monkeypatch.setattr("genea.cli.kbcmds.run_checks", lambda settings: [])
    result = runner.invoke(app, ["--kb", str(root), "doctor"])
    assert result.exit_code == 0 and "1 of 1 have lost spans" in result.output


def patch_conversion(monkeypatch, tmp_path, fail=None):
    calls = []

    def fake_convert(source, target, notify):
        if fail:
            raise fail
        calls.append((source, target.name))
        target.write_bytes(b"ggml")

    monkeypatch.setattr("genea.cli.transcribecmds.models_dir", lambda: tmp_path)
    monkeypatch.setattr("genea.cli.transcribecmds.convert_model", fake_convert)
    return calls


def test_convert_a_known_alias_names_the_model_and_says_how_to_use_it(tmp_path, monkeypatch):
    calls = patch_conversion(monkeypatch, tmp_path)
    result = runner.invoke(app, ["transcribe-model", "convert", "sk-medium"])
    assert result.exit_code == 0, result.output
    assert calls == [("kinit/whisper-medium-sk", "ggml-sk-medium.bin")]
    assert "--model sk-medium" in result.output and (tmp_path / "ggml-sk-medium.bin").is_file()


def test_convert_a_repository_takes_the_name_from_the_repository_or_the_option(tmp_path, monkeypatch):
    calls = patch_conversion(monkeypatch, tmp_path)
    assert runner.invoke(app, ["transcribe-model", "convert", "someone/whisper-small-sk"]).exit_code == 0
    assert runner.invoke(app, ["transcribe-model", "convert", "someone/x", "--name", "mine"]).exit_code == 0
    assert [name for _, name in calls] == ["ggml-whisper-small-sk.bin", "ggml-mine.bin"]


def test_convert_refuses_to_replace_a_model_without_force(tmp_path, monkeypatch):
    calls = patch_conversion(monkeypatch, tmp_path)
    (tmp_path / "ggml-sk-medium.bin").write_bytes(b"old")
    result = runner.invoke(app, ["transcribe-model", "convert", "sk-medium"])
    assert result.exit_code == 1 and "--force" in result.output and calls == []
    assert runner.invoke(app, ["transcribe-model", "convert", "sk-medium", "--force"]).exit_code == 0
    assert len(calls) == 1


def test_convert_failure_is_reported_without_a_traceback(tmp_path, monkeypatch):
    from genea.transcribe.convert import ConvertError

    patch_conversion(monkeypatch, tmp_path, fail=ConvertError("no network"))
    result = runner.invoke(app, ["transcribe-model", "convert", "sk-medium"])
    assert result.exit_code == 1 and "no network" in result.output
