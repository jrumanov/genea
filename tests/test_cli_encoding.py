"""Regression: `genea doctor` must not crash on a cp1252 redirected stdout (Windows default)."""

import os
import subprocess
import sys


def test_doctor_survives_cp1252_redirected_stdout(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text('ollama_url = "http://127.0.0.1:9"\n', encoding="utf-8")
    env = {**os.environ, "GENEA_HOME": str(home), "PYTHONIOENCODING": "cp1252"}
    env.pop("PYTHONUTF8", None)
    proc = subprocess.run(
        [sys.executable, "-c", "from genea.cli.main import app; app()", "doctor"],
        env=env, capture_output=True, timeout=60, cwd=tmp_path,
    )
    err = proc.stderr.decode("utf-8", "replace")
    assert "UnicodeEncodeError" not in err and "Traceback" not in err, err
    out = proc.stdout.decode("utf-8", "replace")
    assert "Ollama" in out and "sqlite-vec" in out, out
    assert proc.returncode == 1  # Ollama unreachable
