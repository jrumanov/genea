import subprocess
from datetime import date
from pathlib import Path

import pytest

from genea.gramps import snapshot
from genea.gramps.snapshot import Exports, SnapshotError, check_repo, open_pr, repo_of, same_tree

PR_URL = "https://github.com/x/y/pull/7"
TODAY = date.today().isoformat()
BRANCH = f"genea/{TODAY}-a-person-I1"


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout


def ged(head: str, name: str = "Anna") -> bytes:
    return f"0 HEAD\r\n1 SOUR {head}\r\n0 @I1@ INDI\r\n1 NAME {name}\r\n0 TRLR\r\n".encode()


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-b", "main")
    git(work, "config", "user.name", "Test")
    git(work, "config", "user.email", "t@example.com")
    git(work, "config", "core.autocrlf", "false")
    (work / "latest.ged").write_bytes(ged("one"))
    git(work, "add", "-A")
    git(work, "commit", "-m", "init")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-u", "origin", "main")
    return work


@pytest.fixture
def run():
    def runner(args, cwd):
        if args[0] == "gh":
            if args[1] == "--version":
                return subprocess.CompletedProcess(args, 0, "gh version 2\n", "")
            return subprocess.CompletedProcess(args, 0, f"Creating pull request\n{PR_URL}\n", "")
        return snapshot.default_runner(args, cwd)

    return runner


def go(repo, run, before, after):
    return open_pr(
        repo, Path("latest.ged"), "a-person-I1", before, after, "body", "genea: a-person-I1 (1 changes)", run
    )


def test_two_commits_when_user_edits_unexported(repo, run):
    go(repo, run, Exports(ged("one", "Edited"), None), Exports(ged("one", "After"), None))
    assert git(repo, "log", "--format=%s", f"main..{BRANCH}").splitlines() == [
        "genea: a-person-I1 (1 changes)",
        f"Snapshot {TODAY} before genea",
    ]


def test_before_skipped_when_only_header_differs(repo, run):
    go(repo, run, Exports(ged("two"), None), Exports(ged("two", "After"), None))
    assert len(git(repo, "log", "--format=%s", f"main..{BRANCH}").splitlines()) == 1


def test_gramps_xml_written_next_to_ged(repo, run):
    go(repo, run, Exports(ged("one"), b"<a/>"), Exports(ged("one", "After"), b"<b/>"))
    assert git(repo, "show", f"{BRANCH}:latest.gramps") == "<b/>"


def test_no_xml_when_after_has_none(repo, run):
    go(repo, run, Exports(ged("one"), None), Exports(ged("one", "After"), None))
    assert "latest.gramps" not in git(repo, "ls-tree", "--name-only", BRANCH)


def test_returns_to_main_and_pr_url(repo, run):
    url = go(repo, run, Exports(ged("one"), None), Exports(ged("one", "After"), None))
    assert url == PR_URL
    assert git(repo, "branch", "--show-current").strip() == "main"


def test_branch_suffix_when_exists(repo, run):
    go(repo, run, Exports(ged("one"), None), Exports(ged("one", "A"), None))
    go(repo, run, Exports(ged("one"), None), Exports(ged("one", "B"), None))
    assert f"{BRANCH}-2" in git(repo, "branch", "--list")


def test_check_repo_refuses_dirty_or_behind(repo, run, tmp_path):
    check_repo(repo, run)
    (repo / "x.txt").write_text("x")
    with pytest.raises(SnapshotError, match="not clean"):
        check_repo(repo, run)
    (repo / "x.txt").unlink()
    git(tmp_path, "clone", str(tmp_path / "origin.git"), "other")
    other = tmp_path / "other"
    git(other, "config", "user.name", "T")
    git(other, "config", "user.email", "t@example.com")
    (other / "n.txt").write_text("n")
    git(other, "add", "-A")
    git(other, "commit", "-m", "n")
    git(other, "push", "origin", "main")
    with pytest.raises(SnapshotError, match="git pull"):
        check_repo(repo, run)


def test_repo_of_rejects_non_repo(tmp_path, run):
    (tmp_path / "plain").mkdir()
    with pytest.raises(SnapshotError):
        repo_of(tmp_path / "plain" / "latest.ged", run)


def test_repo_of_finds_toplevel(repo, run):
    assert repo_of(repo / "latest.ged", run).resolve() == repo.resolve()


def test_crlf_bytes_kept(repo, run):
    after = ged("one", "After")
    go(repo, run, Exports(ged("one"), None), Exports(after, None))
    out = subprocess.run(
        ["git", "show", f"{BRANCH}:latest.ged"], cwd=repo, capture_output=True, check=True
    ).stdout
    assert out == after


def test_failure_returns_to_main_with_branch_name(repo):
    def runner(args, cwd):
        if args[0] == "gh":
            return subprocess.CompletedProcess(args, 0 if args[1] == "--version" else 1, "", "boom")
        return snapshot.default_runner(args, cwd)

    with pytest.raises(SnapshotError, match=BRANCH):
        go(repo, runner, Exports(ged("one"), None), Exports(ged("one", "After"), None))
    assert git(repo, "branch", "--show-current").strip() == "main"


def test_same_tree_ignores_head_record():
    assert same_tree(ged("a"), ged("b"))
    assert not same_tree(ged("a"), ged("a", "Other"))
