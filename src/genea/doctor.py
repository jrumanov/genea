"""Environment checks for `genea doctor`."""

from __future__ import annotations

import importlib.util
import sqlite3
from dataclasses import dataclass

import httpx
import sqlite_vec

from genea.config import Settings
from genea.exiftool import exiftool_version


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def check_sqlite_vec() -> Check:
    try:
        conn = sqlite3.connect(":memory:")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        version = conn.execute("SELECT vec_version()").fetchone()[0]
        conn.close()
        return Check("sqlite-vec", True, version)
    except Exception as exc:  # noqa: BLE001 - any failure means the extension is unusable
        return Check("sqlite-vec", False, str(exc),
                     "Use a Python whose sqlite3 can load extensions (the uv-managed CPython can).")


def check_exiftool(exe: str) -> Check:
    version = exiftool_version(exe)
    if version:
        return Check("ExifTool", True, f"version {version}")
    return Check(
        "ExifTool", False, f"`{exe}` did not run",
        "Install ExifTool (`winget install OliverBetz.ExifTool`, or `choco install exiftool --force` "
        "to repair a Chocolatey install) or set exiftool = '<full path>' in config.toml.",
    )


def ollama_models(url: str, timeout: float = 5.0) -> list[str] | None:
    """Names of pulled Ollama models, or None if Ollama is unreachable."""
    try:
        response = httpx.get(f"{url.rstrip('/')}/api/tags", timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return [m["name"] for m in response.json().get("models", [])]


def _has_model(names: list[str], model: str) -> bool:
    return model in names or f"{model}:latest" in names


def dna_checks() -> list[Check]:
    """Shown only when the dna extra is installed, so doctor stays green for people who do not use DNA."""
    if importlib.util.find_spec("snps") is None:
        return []
    from genea.dna import runtime

    java = runtime.find_java()
    checks = [Check("Java (DNA)", java.path is not None, java.detail,
                    "" if java.path else "genea dna setup (downloads a portable Java into ~/.genea/dna-tools; "
                                         "your system Java is not changed)")]
    installed = runtime.haplogrep_exe().is_file() and runtime.rcrs_fasta().is_file()
    checks.append(Check("Haplogrep", installed,
                        f"{runtime.HAPLOGREP_VERSION} ({runtime.HAPLOGREP_TREE})" if installed else "not installed",
                        "" if installed else "genea dna setup"))
    version = runtime.yhaplo_version()
    checks.append(Check("yhaplo", version is not None, version or "not installed",
                        "" if version else "uv sync --extra dna"))
    return checks


def run_checks(settings: Settings) -> list[Check]:
    checks = [check_sqlite_vec(), check_exiftool(settings.exiftool), *dna_checks()]
    names = ollama_models(settings.ollama_url)
    if names is None:
        checks.append(Check("Ollama", False, f"not reachable at {settings.ollama_url}",
                            "Install Ollama from https://ollama.com and make sure it is running."))
        return checks
    checks.append(Check("Ollama", True, f"reachable at {settings.ollama_url}"))
    wanted = [("Embedding model", settings.embed_model)]
    if settings.model.startswith("ollama:"):
        wanted.append(("Chat model", settings.model.removeprefix("ollama:")))
    for label, model in wanted:
        if _has_model(names, model):
            checks.append(Check(label, True, model))
        else:
            checks.append(Check(label, False, f"{model} is not pulled", f"ollama pull {model}"))
    if not settings.model.startswith("ollama:"):
        checks.append(Check("Chat model", True,
                            f"{settings.model} (cloud; needs its API key in the environment)"))
    return checks
