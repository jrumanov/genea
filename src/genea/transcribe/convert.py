"""Convert a Hugging Face Whisper checkpoint (safetensors) to the ggml .bin that whisper.cpp loads.

A port of whisper.cpp's models/convert-h5-to-ggml.py that needs only numpy and safetensors, not torch.
"""

from __future__ import annotations

import json
import math
import os
import struct
from collections.abc import Callable
from pathlib import Path

GGML_MAGIC = 0x67676D6C
SAMPLE_RATE = 16000
N_FFT = 400

CONVERT_HINT = "Converting a Hugging Face model needs more packages: run `uv sync --extra transcribe --extra convert`."

# Hugging Face layer names -> the names whisper.cpp reads
LAYER_NAMES = {
    "self_attn.k_proj": "attn.key",
    "self_attn.q_proj": "attn.query",
    "self_attn.v_proj": "attn.value",
    "self_attn.out_proj": "attn.out",
    "self_attn_layer_norm": "attn_ln",
    "encoder_attn.q_proj": "cross_attn.query",
    "encoder_attn.v_proj": "cross_attn.value",
    "encoder_attn.out_proj": "cross_attn.out",
    "encoder_attn_layer_norm": "cross_attn_ln",
    "fc1": "mlp.0",
    "fc2": "mlp.2",
    "final_layer_norm": "mlp_ln",
}
OTHER_NAMES = {
    "encoder.layer_norm.bias": "encoder.ln_post.bias",
    "encoder.layer_norm.weight": "encoder.ln_post.weight",
    "encoder.embed_positions.weight": "encoder.positional_embedding",
    "decoder.layer_norm.bias": "decoder.ln.bias",
    "decoder.layer_norm.weight": "decoder.ln.weight",
    "decoder.embed_positions.weight": "decoder.positional_embedding",
    "decoder.embed_tokens.weight": "decoder.token_embedding.weight",
}
KEPT_AS_F32 = {"encoder.conv1.bias", "encoder.conv2.bias", "encoder.positional_embedding",
               "decoder.positional_embedding"}
SKIPPED = {"proj_out.weight"}  # tied to the token embedding; whisper.cpp does not read it
DOWNLOAD_PATTERNS = ["config.json", "vocab.json", "*.safetensors", "*.safetensors.index.json"]


class ConvertError(RuntimeError):
    """The checkpoint is missing, not a Whisper model, or the conversion packages are not installed."""


def ggml_name(hf_name: str) -> str:
    """The whisper.cpp name of a Hugging Face tensor, e.g. model.decoder.layers.0.fc1.weight -> decoder.blocks.0.mlp.0.weight."""
    parts = hf_name.split(".")
    if parts[0] == "model":
        parts = parts[1:]
    if len(parts) > 3 and parts[1] == "layers":
        layer = ".".join(parts[3:-1])
        if layer == "encoder_attn.k_proj":
            mapped = "attn.key" if parts[0] == "encoder" else "cross_attn.key"
        else:
            mapped = LAYER_NAMES.get(layer)
        if mapped is None:
            raise ConvertError(f"Unknown Whisper tensor '{hf_name}'.")
        return ".".join([parts[0], "blocks", parts[2], mapped, parts[-1]])
    name = ".".join(parts)
    return OTHER_NAMES.get(name, name)


def _byte_decoder() -> dict[str, int]:
    """GPT-2's printable stand-ins for bytes, inverted: the vocabulary is stored with these characters."""
    keep = list(range(ord("!"), ord("~") + 1)) + list(range(0xA1, 0xAD)) + list(range(0xAE, 0x100))
    chars, extra = keep[:], 0
    for byte in range(256):
        if byte not in keep:
            keep.append(byte)
            chars.append(256 + extra)
            extra += 1
    return {chr(c): b for b, c in zip(keep, chars, strict=True)}


def _hz_to_mel(hz: float) -> float:
    mel = hz / (200 / 3)
    if hz >= 1000:
        mel = 15 + math.log(hz / 1000) / (math.log(6.4) / 27)
    return mel


def _mel_to_hz(mels):
    import numpy as np

    mels = np.asarray(mels, dtype=np.float64)
    hz = mels * (200 / 3)
    return np.where(mels >= 15, 1000 * np.exp((math.log(6.4) / 27) * (mels - 15)), hz)


def mel_filters(n_mels: int):
    """Whisper's mel filter bank (Slaney scale and norm), shape (n_mels, N_FFT // 2 + 1)."""
    import numpy as np

    freqs = np.linspace(0, SAMPLE_RATE / 2, 1 + N_FFT // 2)
    points = _mel_to_hz(np.linspace(_hz_to_mel(0.0), _hz_to_mel(SAMPLE_RATE / 2), n_mels + 2))
    ramps = points[:, None] - freqs[None, :]
    steps = np.diff(points)
    lower = -ramps[:-2] / steps[:-1, None]
    upper = ramps[2:] / steps[1:, None]
    weights = np.maximum(0, np.minimum(lower, upper))
    return (weights * (2.0 / (points[2:] - points[:-2]))[:, None]).astype(np.float32)


def _load_config(source: Path) -> dict:
    try:
        config = json.loads((source / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConvertError(f"{source} has no readable config.json; is it a Hugging Face model folder?") from exc
    if config.get("model_type") != "whisper":
        raise ConvertError(f"{source} is not a Whisper model (model_type: {config.get('model_type')!r}).")
    return config


def _tensor_files(source: Path) -> list[Path]:
    files = sorted(source.glob("*.safetensors"))
    if not files:
        raise ConvertError(f"{source} has no .safetensors files (only safetensors checkpoints are supported).")
    return files


def convert_checkpoint(source: Path, target: Path) -> None:
    """Write the ggml (float16, small tensors float32) version of the checkpoint folder `source` to `target`."""
    try:
        import numpy as np
        from safetensors import safe_open
    except ImportError as exc:
        raise ConvertError(CONVERT_HINT) from exc
    config = _load_config(source)
    try:
        vocab = json.loads((source / "vocab.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConvertError(f"{source} has no readable vocab.json.") from exc
    files = _tensor_files(source)
    decoder = _byte_decoder()
    n_mels = config["num_mel_bins"]
    header = [config["vocab_size"], config["max_source_positions"], config["d_model"],
              config["encoder_attention_heads"], config["encoder_layers"], config["max_target_positions"],
              config["d_model"], config["decoder_attention_heads"], config["decoder_layers"], n_mels, 1]
    part = target.with_name(target.name + ".part")
    try:
        with part.open("wb") as out:
            out.write(struct.pack("i", GGML_MAGIC))
            out.write(struct.pack(f"{len(header)}i", *header))
            filters = mel_filters(n_mels)
            out.write(struct.pack("ii", *filters.shape))
            out.write(filters.tobytes())
            tokens = sorted(vocab.items(), key=lambda item: item[1])
            out.write(struct.pack("i", len(tokens)))
            for token, _ in tokens:
                try:
                    raw = bytes(decoder[c] for c in token)
                except KeyError:  # a special token that is not byte-encoded text
                    raw = token.encode("utf-8")
                out.write(struct.pack("i", len(raw)))
                out.write(raw)
            for file in files:
                with safe_open(str(file), framework="numpy") as tensors:
                    for key in tensors.keys():  # noqa: SIM118 - safetensors' handle is not a dict
                        if key.removeprefix("model.") in SKIPPED:
                            continue
                        name = ggml_name(key)
                        data = tensors.get_tensor(key).squeeze()
                        if name in ("encoder.conv1.bias", "encoder.conv2.bias"):
                            data = data.reshape(data.shape[0], 1)
                        as_f32 = data.ndim < 2 or name in KEPT_AS_F32
                        data = data.astype(np.float32 if as_f32 else np.float16)
                        encoded = name.encode("utf-8")
                        out.write(struct.pack("iii", data.ndim, len(encoded), 0 if as_f32 else 1))
                        out.write(struct.pack(f"{data.ndim}i", *reversed(data.shape)))
                        out.write(encoded)
                        out.write(data.tobytes())
        os.replace(part, target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def fetch_checkpoint(repo: str) -> Path:
    """Download the files of a Hugging Face model repository (cached by huggingface_hub) and return its folder."""
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")  # symlinks are unavailable on most Windows setups
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise ConvertError(CONVERT_HINT) from exc
    try:
        return Path(snapshot_download(repo, allow_patterns=DOWNLOAD_PATTERNS))
    except Exception as exc:  # noqa: BLE001 - network, auth and unknown-repository errors alike
        raise ConvertError(f"Could not download '{repo}' from Hugging Face ({exc}).") from exc


def convert_model(source: str, target: Path, notify: Callable[[str], None] = lambda message: None) -> Path:
    """`source` is a local checkpoint folder or a Hugging Face repository id such as owner/name."""
    folder = Path(source).expanduser()
    if not folder.is_dir():
        notify(f"Downloading '{source}' from Hugging Face; this happens once.")
        folder = fetch_checkpoint(source)
    notify(f"Converting {source} to {target}; this takes a few minutes.")
    target.parent.mkdir(parents=True, exist_ok=True)
    convert_checkpoint(folder, target)
    return target
