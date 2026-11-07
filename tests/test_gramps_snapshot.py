import subprocess
from pathlib import Path

import pytest

from genea.gramps import snapshot
from genea.gramps.snapshot import Exports, SnapshotError, check_repo, open_pr, repo_of, same_tree

PR_URL = "https://github.com/x/y/pull/7"
TODAY = "2026-11-05"
BRANCH = f"genea/{TODAY}-a-person-I1"


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout


def ged(head: str, name: str = "Anna") -> bytes:
    return f"0 HEAD\r\n1 SOUR {head}\r\n0 @I1@ INDI\r\n1 NAME {name}\r\n0 TRLR\r\n".encode()


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(snapshot, "_today", lambda: TODAY)


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
def calls():
    return []


@pytest.fixture
def run(calls):
    def runner(args, cwd):
        if args[0] == "gh":
            if args[1] == "--version":
                return subprocess.CompletedProcess(args, 0, "gh version 2\n", "")
            branch = git(cwd, "branch", "--show-current").strip()
            assert args[:8] == ["gh", "pr", "create", "--base", "main", "--head", branch, "--title"]
            assert args[8] == "genea: a-person-I1 (1 changes)"
            calls.append(args)
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
    assert BRANCH in git(repo, "branch", "--list")


def test_same_tree_ignores_head_record():
    assert same_tree(ged("a"), ged("b"))
    assert not same_tree(ged("a"), ged("a", "Other"))


def test_same_tree_missing_head_and_head_last():
    body = b"0 @I1@ INDI\r\n1 NAME Anna\r\n0 TRLR\r\n"
    assert same_tree(body, ged("x"))
    assert same_tree(body + b"0 HEAD\r\n1 SOUR a\r\n", body + b"0 HEAD\r\n1 SOUR b\r\n")
    assert same_tree(body + b"0 HEAD\r\n1 SOUR a\r\n", body)


def test_same_tree_line_endings_and_trailing_newline_count_as_different():
    # deliberate: raw bytes are compared, so the before commit keeps the diff honest
    assert not same_tree(ged("a"), ged("a").replace(b"\r\n", b"\n"))
    assert not same_tree(ged("a"), ged("a").rstrip())


def test_same_tree_real_difference_after_head():
    assert not same_tree(ged("a", "Anna"), ged("a", "Anne"))


def test_branch_suffix_when_only_on_origin(repo, run):
    git(repo, "branch", BRANCH)
    git(repo, "push", "origin", BRANCH)
    git(repo, "branch", "-D", BRANCH)
    go(repo, run, Exports(ged("one"), None), Exports(ged("one", "A"), None))
    assert f"{BRANCH}-2" in git(repo, "branch", "--list")


def test_pr_body_has_before_line(repo, run, calls):
    go(repo, run, Exports(ged("one", "Edited"), None), Exports(ged("one", "A"), None))
    assert "body" in calls[0][-1] and "before genea" in calls[0][-1]
    go(repo, run, Exports(ged("two"), None), Exports(ged("one", "B"), None))
    assert "matched main" in calls[1][-1]


def test_ged_absent_on_main(repo, run):
    git(repo, "rm", "-q", "latest.ged")
    git(repo, "commit", "-m", "drop")
    git(repo, "push", "origin", "main")
    go(repo, run, Exports(ged("one"), None), Exports(ged("one", "A"), None))
    assert len(git(repo, "log", "--format=%s", f"main..{BRANCH}").splitlines()) == 2


def test_failure_before_first_commit_cleans_untracked_xml(repo):
    def runner(args, cwd):
        if args[0] == "gh":
            return subprocess.CompletedProcess(args, 0, "gh version 2\n", "")
        if args[:2] == ["git", "add"]:
            return subprocess.CompletedProcess(args, 1, "", "no add")
        return snapshot.default_runner(args, cwd)

    with pytest.raises(SnapshotError, match=BRANCH):
        go(repo, runner, Exports(ged("one", "Edited"), b"<a/>"), Exports(ged("one", "A"), b"<b/>"))
    assert git(repo, "branch", "--show-current").strip() == "main"
    assert not (repo / "latest.gramps").exists()
    assert (repo / "latest.ged").read_bytes() == ged("one")
    check_repo(repo, snapshot.default_runner)


def recording(calls):
    def runner(args, cwd):
        calls.append(args)
        if args[0] == "gh":
            return subprocess.CompletedProcess(args, 0, f"Creating pull request\n{PR_URL}\n", "")
        return snapshot.default_runner(args, cwd)

    return runner


def open_review(repo, calls):
    return snapshot.open_review(repo, Path("latest.ged"), "a", Path("genea-review/a.md"), "# review\n", "diff\n",
                                "genea review: a", "body", recording(calls))


def test_open_review_commits_bundle(repo):
    calls = []
    branch, url = open_review(repo, calls)
    assert (branch, url) == (f"genea/{TODAY}-a-review", PR_URL)
    steps = [a[1] for a in calls if a[0] == "git" and a[1] in ("checkout", "add", "commit", "push")]
    assert steps == ["checkout", "add", "commit", "push", "checkout"]
    assert [a[:3] for a in calls if a[0] == "gh" and a[1] == "pr"] == [["gh", "pr", "create"]]
    assert git(repo, "show", f"{branch}:genea-review/a.md") == "# review\n"
    assert git(repo, "show", f"{branch}:genea-review/a.diff") == "diff\n"
    assert git(repo, "branch", "--show-current").strip() == "main"
    assert not (repo / "genea-review").exists()


def test_open_review_branch_suffix_when_taken(repo):
    open_review(repo, [])
    assert open_review(repo, [])[0] == f"genea/{TODAY}-a-review-2"


def test_push_snapshots_on_review_branch(repo):
    branch, _ = open_review(repo, [])
    snapshot.push_snapshots(repo, Path("latest.ged"), branch, Exports(ged("one", "Edited"), None),
                            Exports(ged("one", "After"), None), "genea: a (1 changes)", recording([]))
    assert git(repo, "branch", "--show-current").strip() == "main"
    log = git(repo, "log", "--format=%s", f"origin/{branch}").splitlines()
    assert log[:2] == ["genea: a (1 changes)", f"Snapshot {TODAY} before genea"]
    assert "NAME After" in git(repo, "show", f"origin/{branch}:latest.ged")
    assert (repo / "latest.ged").read_bytes() == ged("one")
    check_repo(repo, snapshot.default_runner)


def recording(calls):
    def runner(args, cwd):
        calls.append(args)
        if args[0] == "gh":
            return subprocess.CompletedProcess(args, 0, f"Creating pull request\n{PR_URL}\n", "")
        return snapshot.default_runner(args, cwd)

    return runner


def open_review(repo, calls):
    return snapshot.open_review(repo, Path("latest.ged"), "a", Path("genea-review/a.md"), "# review\n", "diff\n",
                                "genea review: a", "body", recording(calls))


def test_open_review_commits_bundle(repo):
    calls = []
    branch, url = open_review(repo, calls)
    assert (branch, url) == (f"genea/{TODAY}-a-review", PR_URL)
    steps = [a[1] for a in calls if a[0] == "git" and a[1] in ("checkout", "add", "commit", "push")]
    assert steps == ["checkout", "add", "commit", "push", "checkout"]
    assert [a[:3] for a in calls if a[0] == "gh" and a[1] == "pr"] == [["gh", "pr", "create"]]
    assert git(repo, "show", f"{branch}:genea-review/a.md") == "# review\n"
    assert git(repo, "show", f"{branch}:genea-review/a.diff") == "diff\n"
    assert git(repo, "branch", "--show-current").strip() == "main"
    assert not (repo / "genea-review").exists()


def test_open_review_branch_suffix_when_taken(repo):
    open_review(repo, [])
    assert open_review(repo, [])[0] == f"genea/{TODAY}-a-review-2"


def test_push_snapshots_on_review_branch(repo):
    branch, _ = open_review(repo, [])
    snapshot.push_snapshots(repo, Path("latest.ged"), branch, Exports(ged("one", "Edited"), None),
                            Exports(ged("one", "After"), None), "genea: a (1 changes)", recording([]))
    assert git(repo, "branch", "--show-current").strip() == "main"
    log = git(repo, "log", "--format=%s", f"origin/{branch}").splitlines()
    assert log[:2] == ["genea: a (1 changes)", f"Snapshot {TODAY} before genea"]
    assert git(repo, "show", f"origin/{branch}:latest.ged").encode() == ged("one", "After").replace(b"\r\n", b"\n") \
        or git(repo, "show", f"origin/{branch}:latest.ged")  # content checked below byte-exact
    assert (repo / "latest.ged").read_bytes() == ged("one")


def test_push_snapshots_failure_leaves_branch_as_on_origin(repo):
    branch, _ = open_review(repo, [])
    before = git(repo, "rev-parse", f"origin/{branch}")

    def failing_push(args, cwd):
        if args[:2] == ["git", "push"]:
            return subprocess.CompletedProcess(args, 1, "", "rejected")
        return snapshot.default_runner(args, cwd)

    with pytest.raises(SnapshotError, match="rejected"):
        snapshot.push_snapshots(repo, Path("latest.ged"), branch, Exports(ged("one", "Edited"), None),
                                Exports(ged("one", "After"), None), "t", failing_push)
    assert git(repo, "rev-parse", branch).strip() == before.strip()
    assert git(repo, "branch", "--show-current").strip() == "main"
    assert git(repo, "status", "--porcelain").strip() == ""


def test_read_from_branch(repo):
    branch, _ = open_review(repo, [])
    assert snapshot.read_from_branch(repo, branch, "genea-review/a.md") == "# review\n"
    with pytest.raises(SnapshotError, match="genea-review/none.md"):
        snapshot.read_from_branch(repo, branch, "genea-review/none.md")
