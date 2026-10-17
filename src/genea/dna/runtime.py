"""The DNA tools under ~/.genea/dna-tools (shared by every knowledge base) and `genea dna setup`.

Haplogrep 3 needs Java 11+. A suitable system Java is used as is; otherwise setup unpacks a portable
Temurin JRE next to Haplogrep. The system Java, PATH and JAVA_HOME are never changed.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from genea.config import global_dir
from genea.dna.store import DnaError

HAPLOGREP_VERSION = "3.3.2"
HAPLOGREP_TREE = "phylotree-fu-rcrs@1.3"
HAPLOGREP_URL = "https://github.com/genepi/haplogrep3/releases/download/v3.3.2/haplogrep3-3.3.2-windows.tar.gz"
HAPLOGREP_SHA256 = "97a808461cdf8ce21d0d00c9a29f8500b29345fae8d22ec99c69d6853033c5f4"
JRE_RELEASE = "jdk-21.0.12.1+1"
JRE_URL = ("https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/"
           "OpenJDK21U-jre_x64_windows_hotspot_21.0.12.1_1.zip")
JRE_SHA256 = "d35f31e712f0fcf6ac5a093edc90204fbff22f720ba3950bd09d331d5e621636"
JAVA_MIN = 11

Fetch = Callable[[str, Path], None]
Run = Callable[..., subprocess.CompletedProcess]

_VERSION = re.compile(r'version "(\d+)(?:\.(\d+))?')


def tools_dir() -> Path:
    return global_dir() / "dna-tools"


def haplogrep_dir() -> Path:
    return tools_dir() / "haplogrep3"


def haplogrep_exe() -> Path:
    return haplogrep_dir() / "haplogrep3.exe"


def rcrs_fasta() -> Path:
    """The rCRS reference that comes with Haplogrep's tree; downloaded on Haplogrep's first start."""
    return haplogrep_dir() / "trees" / "phylotree-fu-rcrs" / "1.3" / "rcrs.fasta"


def portable_java() -> Path:
    return tools_dir() / "jre" / f"{JRE_RELEASE}-jre" / "bin" / "java.exe"


def java_major(java: str, run: Run = subprocess.run) -> int | None:
    """8 for 'java version "1.8.0_503"', 21 for 'openjdk version "21.0.12.1"'; None if it does not run."""
    try:
        done = run([java, "-version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = _VERSION.search((done.stderr or "") + (done.stdout or ""))
    if not match:
        return None
    first = int(match[1])
    return int(match[2]) if first == 1 and match[2] else first


def system_java() -> str | None:
    home = os.environ.get("JAVA_HOME")
    if home:
        for name in ("java.exe", "java"):
            candidate = Path(home) / "bin" / name
            if candidate.is_file():
                return str(candidate)
    return shutil.which("java")


@dataclass(frozen=True)
class JavaChoice:
    path: str | None
    kind: Literal["system", "portable", "none"]
    detail: str


def find_java(run: Run = subprocess.run) -> JavaChoice:
    found = []
    system = system_java()
    if system:
        major = java_major(system, run)
        if major is not None and major >= JAVA_MIN:
            return JavaChoice(system, "system", f"system Java {major} ({system})")
        found.append(f"Java {major} at {system}" if major else f"a Java that does not run at {system}")
    portable = portable_java()
    if portable.is_file():
        major = java_major(str(portable), run)
        if major is not None and major >= JAVA_MIN:
            return JavaChoice(str(portable), "portable", f"portable Java {major} ({portable})")
        found.append(f"a portable Java that does not run at {portable}")
    suffix = f" (found {'; '.join(found)})" if found else ""
    return JavaChoice(None, "none", f"no Java {JAVA_MIN}+ for Haplogrep{suffix}")


def yhaplo_version() -> str | None:
    try:
        return importlib.metadata.version("yhaplo")
    except importlib.metadata.PackageNotFoundError:
        return None


def fetch(url: str, dest: Path) -> None:
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        with dest.open("wb") as f:
            for chunk in response.iter_bytes(1 << 20):
                f.write(chunk)


def download_verified(url: str, sha256: str, dest: Path, fetch_fn: Fetch = fetch) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        fetch_fn(url, part)
        digest = hashlib.sha256(part.read_bytes()).hexdigest()
    except (httpx.HTTPError, OSError) as exc:
        part.unlink(missing_ok=True)
        raise DnaError(f"Download failed: {url} ({exc})") from exc
    if digest != sha256:
        part.unlink(missing_ok=True)
        raise DnaError(f"Checksum mismatch for {url}: expected {sha256}, got {digest}. Nothing was installed.")
    os.replace(part, dest)


def _unpack(archive: Path, final: Path, extract: Callable[[Path], None]) -> None:
    staging = final.with_name(final.name + ".part")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        extract(staging)
    except (tarfile.TarError, zipfile.BadZipFile, OSError) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise DnaError(f"Could not unpack {archive.name}: {exc}") from exc
    finally:
        archive.unlink(missing_ok=True)
    shutil.rmtree(final, ignore_errors=True)
    staging.rename(final)


def install_jre(fetch_fn: Fetch = fetch) -> None:
    archive = tools_dir() / "jre.zip"
    download_verified(JRE_URL, JRE_SHA256, archive, fetch_fn)

    def extract(target: Path) -> None:
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
    _unpack(archive, tools_dir() / "jre", extract)
    if not portable_java().is_file():
        raise DnaError(f"The JRE archive did not contain {portable_java()}.")


def install_haplogrep(java: str, fetch_fn: Fetch = fetch, run: Run = subprocess.run) -> None:
    if not haplogrep_exe().is_file():
        archive = tools_dir() / "haplogrep3.tar.gz"
        download_verified(HAPLOGREP_URL, HAPLOGREP_SHA256, archive, fetch_fn)

        def extract(target: Path) -> None:
            with tarfile.open(archive, "r:gz") as tar:
                tar.extractall(target, filter="data")
        _unpack(archive, haplogrep_dir(), extract)
        if not haplogrep_exe().is_file():
            raise DnaError(f"{haplogrep_exe()} is missing after unpacking; an antivirus may have removed it.")
    if not rcrs_fasta().is_file():
        # Haplogrep downloads its phylotrees (about 60 MB) next to itself on its first start.
        done = run([java, "-jar", str(haplogrep_exe())], capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=1800, cwd=haplogrep_dir())
        if not rcrs_fasta().is_file():
            tail = ((done.stderr or "") + (done.stdout or "")).strip()[-500:]
            raise DnaError(f"Haplogrep did not download its trees: {tail or f'exit code {done.returncode}'}")


def setup(fetch_fn: Fetch = fetch, run: Run = subprocess.run, platform: str = sys.platform) -> list[str]:
    if platform != "win32":
        raise DnaError(f"`genea dna setup` installs the Windows builds only. Elsewhere install Java {JAVA_MIN}+ "
                       f"and unpack Haplogrep {HAPLOGREP_VERSION} into {haplogrep_dir()}.")
    messages = []
    java = find_java(run)
    if java.path is None:
        messages.append(f"{java.detail}; downloading a portable Java ({JRE_RELEASE}, about 50 MB) into "
                        f"{tools_dir()}. Your system Java is not changed.")
        install_jre(fetch_fn)
        java = find_java(run)
        if java.path is None:
            raise DnaError(f"The portable Java does not run: {java.detail}")
    messages.append(f"Java: {java.detail}")
    install_haplogrep(java.path, fetch_fn, run)
    messages.append(f"Haplogrep {HAPLOGREP_VERSION} with {HAPLOGREP_TREE}: {haplogrep_dir()}")
    version = yhaplo_version()
    messages.append(f"yhaplo {version}" if version else "yhaplo is missing: run `uv sync --extra dna`.")
    return messages
