"""Settings: built-in defaults < global ~/.genea/config.toml < <kb>/.genea/config.toml < overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    model: str = "ollama:qwen3:14b"
    embed_model: str = "bge-m3"
    embed_dim: int = 1024
    ollama_url: str = "http://localhost:11434"
    max_tool_calls: int = 15
    tool_output_chars: int = 12000
    privacy_notice: bool = True
    log_prompts: bool = True
    gedcom_direct_write: bool = False
    exclude: tuple[str, ...] = ()
    exiftool: str = "exiftool"
    summary_model: str = ""  # Ollama model for transcript summaries; empty: the chat model, if it is an ollama: one


class KBNotFound(Exception):
    """No usable knowledge-base root could be determined."""


def global_dir() -> Path:
    return Path(os.environ.get("GENEA_HOME") or Path.home() / ".genea")


def _load_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def _apply(settings: Settings, data: dict) -> Settings:
    known = {f.name for f in fields(Settings)}
    updates = {k: (tuple(v) if k == "exclude" else v) for k, v in data.items() if k in known}
    return replace(settings, **updates)


def load_settings(kb_root: Path | None, overrides: dict | None = None) -> Settings:
    settings = _apply(Settings(), _load_toml(global_dir() / "config.toml"))
    if kb_root is not None:
        settings = _apply(settings, _load_toml(Path(kb_root) / ".genea" / "config.toml"))
    return _apply(settings, overrides or {})


def resolve_kb_root(kb: str | None) -> Path:
    """--kb value > GENEA_KB > default_kb; names are looked up in [kbs] of the global config."""
    gcfg = _load_toml(global_dir() / "config.toml")
    value = kb or os.environ.get("GENEA_KB") or gcfg.get("default_kb")
    if not value:
        raise KBNotFound(
            "No knowledge base given: use --kb <folder>, set GENEA_KB, "
            "or set default_kb in ~/.genea/config.toml"
        )
    path = Path(gcfg.get("kbs", {}).get(value, value)).expanduser()
    if not path.is_dir():
        raise KBNotFound(f"Knowledge base folder not found: {path}")
    return path.resolve()
