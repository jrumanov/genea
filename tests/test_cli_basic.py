from typer.testing import CliRunner

from genea.cli.main import app
from genea.doctor import Check

runner = CliRunner()


def test_init_creates_meta_dir(tmp_path):
    result = runner.invoke(app, ["--kb", str(tmp_path), "init"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".genea" / "config.toml").is_file()


def test_init_rejects_missing_folder(tmp_path):
    result = runner.invoke(app, ["--kb", str(tmp_path / "nope"), "init"])
    assert result.exit_code == 2


def test_doctor_exit_codes(monkeypatch):
    monkeypatch.setattr("genea.cli.kbcmds.run_checks",
                        lambda settings: [Check("ExifTool", False, "not found", "install it")])
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "install it" in result.output

    monkeypatch.setattr("genea.cli.kbcmds.run_checks",
                        lambda settings: [Check("ExifTool", True, "13.33")])
    assert runner.invoke(app, ["doctor"]).exit_code == 0
