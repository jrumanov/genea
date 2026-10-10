"""whisper.cpp through pywhispercpp: language and model resolution, and the transcriber."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from genea.ingest.transcript import Segment
from genea.transcribe.convert import ConvertError, convert_model

Transcriber = Callable[..., None]  # transcriber(samples, language, on_segment); times are seconds from samples[0]

EXTRA_HINT = "Transcription is not installed: run `uv sync --extra transcribe`."
APPROX_SIZE = {"tiny": "75 MB", "base": "142 MB", "small": "466 MB", "medium": "1.5 GB",
               "large-v1": "2.9 GB", "large-v2": "2.9 GB", "large-v3": "2.9 GB", "large-v3-turbo": "1.6 GB"}
# Slovak fine-tunes published only as Hugging Face checkpoints: converted to ggml on first use
HF_MODELS = {"sk-medium": "kinit/whisper-medium-sk",  # slaih.sk/en/catalog/whisper-medium-sk
             "sk-small": "NaiveNeuron/whisper-small-sk",
             "sk-large-v3-turbo": "NaiveNeuron/whisper-large-v3-turbo-sk"}


class ModelUnavailable(RuntimeError):
    """The model cannot be found or downloaded, or pywhispercpp is not installed."""


def resolve_language(flag: str | None, setting: str) -> str:
    """--language beats the setting; empty or "auto" means whisper.cpp detects the language ("")."""
    value = (flag or setting or "").strip().lower()
    return "" if value in ("", "auto") else value


def language_label(language: str) -> str:
    return language or "auto-detect"


def _pywhispercpp():
    try:
        from pywhispercpp import constants, utils
    except ImportError as exc:
        raise ModelUnavailable(EXTRA_HINT) from exc
    return constants, utils


def models_dir() -> Path:
    """Where whisper.cpp models are kept."""
    return Path(_pywhispercpp()[0].MODELS_DIR)


def resolve_model(name: str, notify: Callable[[str], None] = lambda message: None, *,
                  constants=None, utils=None, convert=convert_model) -> Path:
    """A file path is used as it is. Otherwise `name` is a model already in the models folder (also one made by
    `genea transcribe-model convert`), a Hugging Face model of HF_MODELS (converted on first use), or a known
    whisper.cpp model name (downloaded on first use)."""
    path = Path(name).expanduser()
    if path.is_file():
        return path.resolve()
    if constants is None or utils is None:
        constants, utils = _pywhispercpp()
    target = Path(constants.MODELS_DIR) / f"ggml-{name}.bin"
    if target.is_file():
        return target
    if name in HF_MODELS:
        try:
            return convert(HF_MODELS[name], target, notify)
        except ConvertError as exc:
            raise ModelUnavailable(str(exc)) from exc
    if name not in constants.AVAILABLE_MODELS:
        raise ModelUnavailable(
            f"'{name}' is neither a file nor a known model name. Known names: "
            f"{', '.join([*constants.AVAILABLE_MODELS, *HF_MODELS])}. Set transcribe_model to the path of a "
            "ggml .bin file to use your own, or make one from a Hugging Face model with "
            "`genea transcribe-model convert`.")
    notify(f"Downloading model '{name}' ({APPROX_SIZE.get(name, 'size varies')}) to "
           f"{constants.MODELS_DIR}; this happens once.")
    try:
        downloaded = utils.download_model(name, constants.MODELS_DIR)
    except Exception as exc:  # noqa: BLE001 - network and disk errors alike
        raise ModelUnavailable(_download_failed(name, constants, exc)) from exc
    if not downloaded:
        raise ModelUnavailable(_download_failed(name, constants, "no file was written"))
    return Path(downloaded)


def _download_failed(name: str, constants, reason) -> str:
    return (f"Could not download model '{name}' to {constants.MODELS_DIR} ({reason}). Download a ggml model "
            "yourself and set transcribe_model to its path (the models of Audacity's OpenVINO plugin work).")


class WhisperCpp:
    """The real transcriber. Loading the model takes a few seconds; one instance is used per run."""

    detected_language = ""  # set by a call that auto-detected the language, so a second pass can reuse it

    def __init__(self, model_path: Path, say: Callable[[str], None] = lambda message: None) -> None:
        try:
            from pywhispercpp.model import Model
        except ImportError as exc:
            raise ModelUnavailable(EXTRA_HINT) from exc
        self._say = say
        self._model = Model(str(model_path), n_threads=os.cpu_count() or 4, print_progress=False,
                            print_realtime=False, redirect_whispercpp_logs_to=os.devnull)

    def __call__(self, samples, language: str, on_segment: Callable[[Segment], None]) -> None:
        self.detected_language = ""
        if not language:
            try:
                detected = self._model.auto_detect_language(samples)[0][0]
            except Exception:  # noqa: BLE001 - whisper.cpp then detects the language itself
                detected = ""
            if detected:
                self._say(f"Detected language: {detected}")
                language = detected  # passed on, so whisper.cpp does not detect it a second time
                self.detected_language = detected

        def callback(segment) -> None:
            text = segment.text.strip()
            if text:
                on_segment(Segment(segment.t0 / 100, segment.t1 / 100, text))  # whisper.cpp counts in 10 ms

        self._model.transcribe(samples, language=language, no_context=True, new_segment_callback=callback)
