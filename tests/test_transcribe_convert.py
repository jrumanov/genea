import json
import struct

import pytest

np = pytest.importorskip("numpy")
safetensors_numpy = pytest.importorskip("safetensors.numpy")

from genea.transcribe import convert  # noqa: E402
from genea.transcribe.convert import ConvertError, convert_checkpoint, convert_model, ggml_name, mel_filters  # noqa: E402

D, HEADS, LAYERS, MELS, CTX_AUDIO, CTX_TEXT = 8, 2, 1, 4, 6, 5


def tiny_checkpoint(folder, *, model_type="whisper"):
    """A random Whisper-shaped checkpoint with the tensor names transformers writes."""
    rng = np.random.default_rng(0)
    vocab = {"a": 0, "b": 1, "Ġc": 2, "<|endoftext|>": 3}
    (folder / "config.json").write_text(json.dumps({
        "model_type": model_type, "vocab_size": 6, "max_source_positions": CTX_AUDIO, "d_model": D,
        "encoder_attention_heads": HEADS, "encoder_layers": LAYERS, "max_target_positions": CTX_TEXT,
        "decoder_attention_heads": HEADS, "decoder_layers": LAYERS, "num_mel_bins": MELS}), encoding="utf-8")
    (folder / "vocab.json").write_text(json.dumps(vocab), encoding="utf-8")

    def t(*shape):
        return rng.standard_normal(shape).astype(np.float32)

    tensors = {
        "model.encoder.conv1.weight": t(D, MELS, 3), "model.encoder.conv1.bias": t(D),
        "model.encoder.conv2.weight": t(D, D, 3), "model.encoder.conv2.bias": t(D),
        "model.encoder.embed_positions.weight": t(CTX_AUDIO, D),
        "model.encoder.layer_norm.weight": t(D), "model.encoder.layer_norm.bias": t(D),
        "model.decoder.embed_tokens.weight": t(6, D), "model.decoder.embed_positions.weight": t(CTX_TEXT, D),
        "model.decoder.layer_norm.weight": t(D), "model.decoder.layer_norm.bias": t(D),
        "proj_out.weight": t(6, D),
    }
    for side in ("encoder", "decoder"):
        base = f"model.{side}.layers.0."
        attentions = ["self_attn"] + (["encoder_attn"] if side == "decoder" else [])
        for att in attentions:
            for proj in ("k_proj", "q_proj", "v_proj", "out_proj"):
                tensors[f"{base}{att}.{proj}.weight"] = t(D, D)
                if proj != "k_proj":
                    tensors[f"{base}{att}.{proj}.bias"] = t(D)
            tensors[f"{base}{att}_layer_norm.weight"] = t(D)
            tensors[f"{base}{att}_layer_norm.bias"] = t(D)
        tensors[f"{base}fc1.weight"], tensors[f"{base}fc1.bias"] = t(4 * D, D), t(4 * D)
        tensors[f"{base}fc2.weight"], tensors[f"{base}fc2.bias"] = t(D, 4 * D), t(D)
        tensors[f"{base}final_layer_norm.weight"] = t(D)
        tensors[f"{base}final_layer_norm.bias"] = t(D)
    safetensors_numpy.save_file(tensors, str(folder / "model.safetensors"))
    return tensors


def read_ggml(path):
    data = path.read_bytes()
    pos = 0

    def take(fmt):
        nonlocal pos
        values = struct.unpack_from(fmt, data, pos)
        pos += struct.calcsize(fmt)
        return values

    assert take("i") == (convert.GGML_MAGIC,)
    hparams = take("11i")
    n_mel, n_fft = take("ii")
    pos += 4 * n_mel * n_fft
    (n_tokens,) = take("i")
    tokens = []
    for _ in range(n_tokens):
        (size,) = take("i")
        tokens.append(data[pos:pos + size])
        pos += size
    tensors = {}
    while pos < len(data):
        n_dims, name_len, ftype = take("iii")
        shape = take(f"{n_dims}i")[::-1]
        name = data[pos:pos + name_len].decode()
        pos += name_len
        count = int(np.prod(shape)) if shape else 1
        dtype = np.float16 if ftype == 1 else np.float32
        tensors[name] = (ftype, np.frombuffer(data, dtype, count, pos).reshape(shape))
        pos += count * np.dtype(dtype).itemsize
    return hparams, (n_mel, n_fft), tokens, tensors


@pytest.mark.parametrize(("hf", "ours"), [
    ("model.decoder.layers.3.fc1.weight", "decoder.blocks.3.mlp.0.weight"),
    ("model.encoder.layers.0.self_attn.k_proj.weight", "encoder.blocks.0.attn.key.weight"),
    ("model.decoder.layers.1.encoder_attn.k_proj.weight", "decoder.blocks.1.cross_attn.key.weight"),
    ("model.decoder.layers.1.encoder_attn_layer_norm.bias", "decoder.blocks.1.cross_attn_ln.bias"),
    ("model.encoder.layer_norm.weight", "encoder.ln_post.weight"),
    ("model.decoder.embed_positions.weight", "decoder.positional_embedding"),
    ("model.decoder.embed_tokens.weight", "decoder.token_embedding.weight"),
    ("model.encoder.conv1.bias", "encoder.conv1.bias"),
])
def test_tensor_names(hf, ours):
    assert ggml_name(hf) == ours


def test_an_unknown_layer_is_an_error():
    with pytest.raises(ConvertError):
        ggml_name("model.encoder.layers.0.mystery.weight")


def test_a_tiny_checkpoint_converts_to_the_layout_whisper_cpp_reads(tmp_path):
    source = tmp_path / "hf"
    source.mkdir()
    original = tiny_checkpoint(source)
    target = tmp_path / "ggml-tiny.bin"
    convert_checkpoint(source, target)
    hparams, filters, tokens, tensors = read_ggml(target)
    assert hparams == (6, CTX_AUDIO, D, HEADS, LAYERS, CTX_TEXT, D, HEADS, LAYERS, MELS, 1)
    assert filters == (MELS, 201)
    assert tokens == [b"a", b"b", b" c", b"<|endoftext|>"]  # Ġ is the byte-encoded space
    assert "decoder.proj.weight" not in tensors and "proj_out.weight" not in tensors
    assert len(tensors) == len(original) - 1
    ftype, conv_bias = tensors["encoder.conv1.bias"]
    assert ftype == 0 and conv_bias.shape == (D, 1)  # conv biases are float32, [n] reshaped to [n, 1]
    ftype, weight = tensors["decoder.blocks.0.mlp.0.weight"]
    assert ftype == 1 and weight.shape == (4 * D, D)
    np.testing.assert_allclose(weight, original["model.decoder.layers.0.fc1.weight"], atol=1e-2)
    assert tensors["encoder.positional_embedding"][0] == 0  # positional embeddings stay float32
    assert not target.with_name(target.name + ".part").exists()


def test_a_failed_conversion_leaves_no_file(tmp_path):
    source = tmp_path / "hf"
    source.mkdir()
    tiny_checkpoint(source)
    safetensors_numpy.save_file({"model.encoder.layers.0.mystery.weight": np.zeros(2, np.float32)},
                                str(source / "z.safetensors"))
    with pytest.raises(ConvertError):
        convert_checkpoint(source, tmp_path / "out.bin")
    assert list(tmp_path.glob("out.bin*")) == []


def test_other_models_and_empty_folders_are_refused(tmp_path):
    source = tmp_path / "hf"
    source.mkdir()
    with pytest.raises(ConvertError, match="config.json"):
        convert_checkpoint(source, tmp_path / "out.bin")
    tiny_checkpoint(source, model_type="bert")
    with pytest.raises(ConvertError, match="not a Whisper model"):
        convert_checkpoint(source, tmp_path / "out.bin")
    (source / "model.safetensors").unlink()
    (source / "config.json").write_text(json.dumps({"model_type": "whisper"}), encoding="utf-8")
    with pytest.raises(ConvertError, match="safetensors"):
        convert_checkpoint(source, tmp_path / "out.bin")


def test_mel_filters_have_whispers_shape_and_normalisation():
    for n_mels in (80, 128):
        filters = mel_filters(n_mels)
        assert filters.shape == (n_mels, 201) and filters.dtype == np.float32
        assert (filters >= 0).all() and (filters.max(axis=1) > 0).all()


def test_a_repository_id_is_downloaded_first_and_a_folder_is_not(tmp_path, monkeypatch):
    source = tmp_path / "hf"
    source.mkdir()
    tiny_checkpoint(source)
    fetched = []
    monkeypatch.setattr(convert, "fetch_checkpoint", lambda repo: fetched.append(repo) or source)
    notes = []
    convert_model("owner/name", tmp_path / "models" / "a.bin", notes.append)
    assert fetched == ["owner/name"] and (tmp_path / "models" / "a.bin").is_file() and len(notes) == 2
    convert_model(str(source), tmp_path / "b.bin")
    assert fetched == ["owner/name"]
