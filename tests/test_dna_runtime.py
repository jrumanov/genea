import hashlib
import io
import subprocess
import tarfile
import zipfile

import pytest

from genea.dna import runtime
from genea.dna.store import DnaError


def _done(cmd, out="", err="", code=0):
    return subprocess.CompletedProcess(cmd, code, out, err)


@pytest.mark.parametrize(("text", "major"), [('java version "1.8.0_503"', 8),
                                              ('openjdk version "21.0.12.1" 2026-08-18 LTS', 21),
                                              ('openjdk version "11" 2018-09-25', 11), ("garbage", None)])
def test_java_major(text, major):
    assert runtime.java_major("java", lambda cmd, **kw: _done(cmd, err=text)) == major


def test_java_major_missing_binary():
    def run(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    assert runtime.java_major("nope", run) is None


def test_find_java_prefers_new_system_java(monkeypatch):
    monkeypatch.setattr(runtime, "system_java", lambda: "C:/jdk17/bin/java.exe")
    choice = runtime.find_java(lambda cmd, **kw: _done(cmd, err='openjdk version "17.0.1"'))
    assert (choice.path, choice.kind) == ("C:/jdk17/bin/java.exe", "system")


def test_find_java_falls_back_to_portable(monkeypatch):
    monkeypatch.setattr(runtime, "system_java", lambda: "C:/java8/bin/java.exe")
    portable = runtime.portable_java()
    portable.parent.mkdir(parents=True)
    portable.write_bytes(b"")

    def run(cmd, **kw):
        return _done(cmd, err='java version "1.8.0_503"' if "java8" in cmd[0] else 'openjdk version "21.0.12.1"')
    choice = runtime.find_java(run)
    assert (choice.path, choice.kind) == (str(portable), "portable")


def test_find_java_reports_old_java(monkeypatch):
    monkeypatch.setattr(runtime, "system_java", lambda: "C:/java8/bin/java.exe")
    choice = runtime.find_java(lambda cmd, **kw: _done(cmd, err='java version "1.8.0_503"'))
    assert choice.path is None and "Java 8" in choice.detail and "11+" in choice.detail


def _fake_archives(monkeypatch):
    """A Haplogrep tar.gz and a JRE zip with the layout of the real ones; their checksums replace the pinned ones."""
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w:gz") as tar:
        for name, data in (("haplogrep3.exe", b"jar"), ("haplogrep3.yaml", b"port: 7000\n")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    zip_bytes = io.BytesIO()
    with zipfile.ZipFile(zip_bytes, "w") as z:
        z.writestr(f"{runtime.JRE_RELEASE}-jre/bin/java.exe", b"exe")
    blobs = {runtime.HAPLOGREP_URL: tar_bytes.getvalue(), runtime.JRE_URL: zip_bytes.getvalue()}
    monkeypatch.setattr(runtime, "HAPLOGREP_SHA256", hashlib.sha256(blobs[runtime.HAPLOGREP_URL]).hexdigest())
    monkeypatch.setattr(runtime, "JRE_SHA256", hashlib.sha256(blobs[runtime.JRE_URL]).hexdigest())
    fetched = []

    def fetch(url, dest):
        fetched.append(url)
        dest.write_bytes(blobs[url])
    return fetch, fetched


def _tool_run(cmd, **kw):
    """java -version answers 21 for the portable JRE and 8 otherwise; java -jar downloads the trees."""
    if cmd[1] == "-version":
        return _done(cmd, err='openjdk version "21.0.12.1"' if "dna-tools" in cmd[0] else 'java version "1.8.0"')
    runtime.rcrs_fasta().parent.mkdir(parents=True, exist_ok=True)
    runtime.rcrs_fasta().write_text(">rCRS\nGATC\n", encoding="ascii")
    return _done(cmd, out="Tree phylotree-fu-rcrs@1.3 installed.")


def test_setup_installs_portable_jre_and_haplogrep(monkeypatch):
    monkeypatch.setattr(runtime, "system_java", lambda: "C:/java8/bin/java.exe")
    monkeypatch.setattr(runtime, "yhaplo_version", lambda: "2.1.19")
    fetch, fetched = _fake_archives(monkeypatch)
    messages = runtime.setup(fetch, _tool_run, platform="win32")
    assert fetched == [runtime.JRE_URL, runtime.HAPLOGREP_URL]
    assert runtime.portable_java().is_file() and runtime.haplogrep_exe().is_file() and runtime.rcrs_fasta().is_file()
    assert any("portable" in m for m in messages) and any("yhaplo 2.1.19" in m for m in messages)
    assert not list(runtime.tools_dir().glob("*.part")) and not list(runtime.tools_dir().glob("*.zip"))
    assert runtime.setup(fetch, _tool_run, platform="win32")  # second run downloads nothing
    assert fetched == [runtime.JRE_URL, runtime.HAPLOGREP_URL]


def test_checksum_mismatch_installs_nothing(monkeypatch):
    monkeypatch.setattr(runtime, "system_java", lambda: None)
    fetch, _ = _fake_archives(monkeypatch)
    monkeypatch.setattr(runtime, "JRE_SHA256", "0" * 64)
    with pytest.raises(DnaError, match="Checksum mismatch"):
        runtime.setup(fetch, _tool_run, platform="win32")
    assert not (runtime.tools_dir() / "jre").exists()
    assert not list(runtime.tools_dir().glob("jre*"))


def test_setup_needs_windows():
    with pytest.raises(DnaError, match="Java 11"):
        runtime.setup(platform="linux")


def test_tools_dir_follows_genea_home(isolated_home):
    assert runtime.tools_dir() == isolated_home / "dna-tools"
