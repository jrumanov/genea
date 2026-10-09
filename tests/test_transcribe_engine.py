import sys
from types import SimpleNamespace

import pytest

from genea.transcribe.engine import (
    EXTRA_HINT,
    ModelUnavailable,
    WhisperCpp,
    language_label,
    resolve_language,
    resolve_model,
)


@pytest.mark.parametrize(("flag", "setting", "expected"), [
    ("sk", "cs", "sk"), (None, "cs", "cs"), ("", "cs", "cs"), (None, "", ""), ("AUTO", "sk", ""),
    (None, "Auto", ""), (" SK ", "", "sk"),
])
def test_language_resolution(flag, setting, expected):
    assert resolve_language(flag, setting) == expected


def test_language_label():
    assert language_label("") == "auto-detect"
    assert language_label("sk") == "sk"


def fakes(tmp_path, downloaded=True):
    models = tmp_path / "models"
    log = []

    def download_model(name, folder):
        log.append(name)
        if not downloaded:
            return None
        models.mkdir(exist_ok=True)
        target = models / f"ggml-{name}.bin"
        target.write_bytes(b"model")
        return str(target)

    constants = SimpleNamespace(AVAILABLE_MODELS=["tiny", "medium", "large-v3"], MODELS_DIR=models)
    return constants, SimpleNamespace(download_model=download_model), log


def test_an_existing_file_is_used_as_it_is(tmp_path):
    model = tmp_path / "my.bin"
    model.write_bytes(b"x")
    constants, utils, log = fakes(tmp_path)
    assert resolve_model(str(model), constants=constants, utils=utils) == model.resolve()
    assert log == []


def test_a_known_name_is_downloaded_once_with_a_notice(tmp_path):
    constants, utils, log = fakes(tmp_path)
    notes = []
    first = resolve_model("medium", notes.append, constants=constants, utils=utils)
    assert first.name == "ggml-medium.bin" and first.is_file()
    assert len(notes) == 1 and "medium" in notes[0]
    resolve_model("medium", notes.append, constants=constants, utils=utils)
    assert log == ["medium"] and len(notes) == 1  # cached the second time, no notice


def test_unknown_model_name_lists_the_valid_ones(tmp_path):
    constants, utils, log = fakes(tmp_path)
    with pytest.raises(ModelUnavailable) as err:
        resolve_model("mediun", constants=constants, utils=utils)
    assert "tiny, medium, large-v3" in str(err.value)
    assert log == []  # nothing was downloaded


def test_a_failed_download_names_the_model_and_the_folder(tmp_path):
    constants, utils, _ = fakes(tmp_path, downloaded=False)
    with pytest.raises(ModelUnavailable) as err:
        resolve_model("medium", constants=constants, utils=utils)
    assert "medium" in str(err.value) and str(constants.MODELS_DIR) in str(err.value)
    assert "transcribe_model" in str(err.value)


def test_without_pywhispercpp_a_model_name_and_the_transcriber_explain_the_extra(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pywhispercpp", None)  # makes the import fail like a missing extra
    with pytest.raises(ModelUnavailable) as err:
        resolve_model("medium")
    assert str(err.value) == EXTRA_HINT
    with pytest.raises(ModelUnavailable) as err:
        WhisperCpp(tmp_path / "ggml-medium.bin")
    assert str(err.value) == EXTRA_HINT


class FakeModel:
    def __init__(self, detected="sk", fails=False):
        self.detected, self.fails, self.calls = detected, fails, []

    def auto_detect_language(self, samples):
        self.calls.append("detect")
        if self.fails:
            raise RuntimeError("too short")
        return (self.detected, 0.9), {}

    def transcribe(self, samples, language, no_context, new_segment_callback):
        self.calls.append(("transcribe", language))
        new_segment_callback(SimpleNamespace(t0=150, t1=320, text=" Dobrý deň. "))


def whisper_with(model):
    said = []
    transcriber = WhisperCpp.__new__(WhisperCpp)  # skips loading a real model
    transcriber._model, transcriber._say = model, said.append
    return transcriber, said


def test_auto_language_is_detected_once_and_passed_on():
    model = FakeModel("sk")
    transcriber, said = whisper_with(model)
    segments = []
    transcriber([0.0] * 10, "", segments.append)
    assert model.calls == ["detect", ("transcribe", "sk")]  # whisper.cpp does not detect it a second time
    assert said == ["Detected language: sk"]
    assert [(s.start, s.end, s.text) for s in segments] == [(1.5, 3.2, "Dobrý deň.")]


def test_a_failed_detection_leaves_it_to_whisper_cpp_and_a_set_language_skips_it():
    model = FakeModel(fails=True)
    transcriber, said = whisper_with(model)
    transcriber([0.0], "", lambda s: None)
    assert model.calls == ["detect", ("transcribe", "")] and said == []
    model = FakeModel()
    transcriber, _ = whisper_with(model)
    transcriber([0.0], "cs", lambda s: None)
    assert model.calls == [("transcribe", "cs")]
