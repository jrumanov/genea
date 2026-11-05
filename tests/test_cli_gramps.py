import sys
import types

import pytest
from test_analysis_apply import CITE, GED, PR_URL, _analysis, _update, ctx, fake, prs, repo, run, setup  # noqa: F401
from typer.testing import CliRunner

from genea.analysis import api, store
from genea.cli.main import app
from genea.index.embed import FakeEmbedder

runner = CliRunner()


@pytest.fixture
def cli(ctx, fake, run, monkeypatch):  # noqa: F811
    root = ctx.kb.root
    (root / ".genea" / "config.toml").write_text(
        f'gramps_web_url = "https://gw.test"\n[gramps_web_trees]\n"{GED}" = "me"\n', encoding="utf-8")
    monkeypatch.setattr("genea.cli.common.get_embedder", lambda kb: FakeEmbedder())
    monkeypatch.setattr("genea.cli.grampscmds.TRANSPORT", fake.transport)
    monkeypatch.setattr("genea.cli.grampscmds.RUNNER", run)
    return lambda *args, **kw: runner.invoke(app, ["--kb", str(root), *args], **kw)


def _built(ctx):
    name = _analysis(ctx)
    pid = _update(ctx, name, "@I3@", "2 JAN 1878")
    api.build_iteration(ctx, name)
    return name, pid


def _writes(fake):
    return [r for r in fake.requests if r[0] in ("POST", "PUT") and "/api/token" not in str(r[1])]


def test_apply_dry_run_writes_nothing(ctx, cli, fake):
    name, pid = _built(ctx)
    result = cli("analysis", "apply", name, "--dry-run")
    assert result.exit_code == 0, result.output
    assert pid in result.output and "+2 DATE 2 JAN 1878" in result.output
    assert _writes(fake) == []


def test_apply_needs_confirmation(ctx, cli, fake):
    name, pid = _built(ctx)
    result = cli("analysis", "apply", name, input="n\n")
    assert "Write 1 change(s) to Gramps Web?" in result.output
    assert _writes(fake) == []
    assert store.load(ctx.kb, name).proposals[0].status == "active"


def test_apply_yes_prints_pr(ctx, cli, fake):
    name, pid = _built(ctx)
    result = cli("analysis", "apply", name, "--yes")
    assert result.exit_code == 0, result.output
    assert PR_URL in result.output and "Merge the PR, git pull, then genea index." in result.output
    assert store.load(ctx.kb, name).proposals[0].status == "written"


def test_apply_precondition_failure_exits_1(ctx, cli, fake):
    name = _analysis(ctx)
    result = cli("analysis", "apply", name, "--yes")
    assert result.exit_code == 1 and "analysis build" in result.output
    assert fake.requests == []


def test_undo_yes(ctx, cli, fake):
    name, pid = _built(ctx)
    assert cli("analysis", "apply", name, "--yes").exit_code == 0
    result = cli("analysis", "undo", name, pid, "--yes")
    assert result.exit_code == 0, result.output
    assert store.load(ctx.kb, name).proposals[0].status == "active"


def test_snapshot_prints_pr(cli, fake, ctx):
    fake.add("people", {"handle": "hnew", "gramps_id": "I99", "profile": {}})
    result = cli("snapshot", GED)
    assert result.exit_code == 0, result.output
    assert PR_URL in result.output


def test_login_stores_password(cli, fake, monkeypatch):
    stored = []
    monkeypatch.setitem(sys.modules, "keyring",
                        types.SimpleNamespace(set_password=lambda *a: stored.append(a), get_password=lambda *a: None))
    monkeypatch.delenv("GENEA_GRAMPS_PASSWORD_ME")
    result = cli("gramps", "login", GED, input="pw\n")
    assert result.exit_code == 0, result.output
    assert stored == [("genea-gramps-web", "me", "pw")]
    assert "pw" not in result.output.replace("password", "")
    assert fake.version in result.output


def test_check_warns_without_delete(cli, fake):
    fake.permissions = ["ViewPrivate", "AddObject", "EditObject"]
    result = cli("gramps", "check")
    assert result.exit_code == 0, result.output
    assert "AddObject: yes" in result.output and "DeleteObject: no" in result.output
    assert "DeleteObject is missing" in result.output


def test_check_all_permissions(cli, fake):
    result = cli("gramps", "check", GED)
    assert "DeleteObject: yes" in result.output and "missing" not in result.output


def test_missing_credentials_exit_1(cli, monkeypatch):
    monkeypatch.delenv("GENEA_GRAMPS_PASSWORD_ME")
    monkeypatch.setitem(sys.modules, "keyring", types.SimpleNamespace(get_password=lambda *a: None))
    result = cli("gramps", "check")
    assert result.exit_code == 1 and "genea gramps login" in result.output


def test_apply_failed_proposal_exits_1(ctx, cli, fake):
    name, pid = _built(ctx)
    fake.fail_on[("PUT", "/api/events/")] = 500
    result = cli("analysis", "apply", name, "--yes")
    assert result.exit_code == 1, result.output
    assert f"failed: {pid}" in result.output


def test_login_keyring_error_is_clean(cli, fake, monkeypatch):
    def boom(*a):
        raise RuntimeError("secret pw detail")

    monkeypatch.setitem(sys.modules, "keyring", types.SimpleNamespace(set_password=boom, get_password=lambda *a: None))
    result = cli("gramps", "login", GED, input="pw\n")
    assert result.exit_code == 1
    assert "login ok, but the password could not be stored: RuntimeError" in result.output
    assert "GENEA_GRAMPS_PASSWORD_ME" in result.output and "secret" not in result.output
    assert "Traceback" not in result.output
