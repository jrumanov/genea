"""Record Gramps Web exports as a before/after snapshot PR in the tree's git repo."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

Runner = Callable[[list[str], Path], subprocess.CompletedProcess]


class SnapshotError(Exception):
    pass


def default_runner(args: list[str], repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=repo, capture_output=True, text=True, encoding="utf-8", check=False)


@dataclass
class Exports:
    ged: bytes
    xml: bytes | None


def _ok(run: Runner, args: list[str], repo: Path) -> str:
    try:
        done = run(args, repo)
    except OSError as e:
        raise SnapshotError(f"cannot run {args[0]}: {e}") from e
    if done.returncode != 0:
        raise SnapshotError(f"`{' '.join(args)}` failed: {(done.stderr or done.stdout).strip()}")
    return done.stdout


def repo_of(ged_path: Path, run: Runner = default_runner) -> Path:
    folder = Path(ged_path).resolve().parent
    try:
        done = run(["git", "rev-parse", "--show-toplevel"], folder)
    except OSError as e:
        raise SnapshotError(f"cannot run git: {e}") from e
    if done.returncode != 0:
        raise SnapshotError(f"{folder} is not inside a git repository")
    return Path(done.stdout.strip())


def check_repo(repo: Path, run: Runner = default_runner) -> None:
    if _ok(run, ["git", "branch", "--show-current"], repo).strip() != "main":
        raise SnapshotError("the tree repo is not on main: run `git checkout main`")
    if _ok(run, ["git", "status", "--porcelain"], repo).strip():
        raise SnapshotError("the tree repo is not clean: commit or stash your changes first")
    _ok(run, ["git", "fetch", "origin"], repo)
    if _ok(run, ["git", "rev-list", "--count", "main..origin/main"], repo).strip() != "0":
        raise SnapshotError("main is behind origin/main: run `git pull` first")
    try:
        done = run(["gh", "--version"], repo)
    except OSError:
        done = None
    if done is None or done.returncode != 0:
        raise SnapshotError("the GitHub CLI `gh` is not available: install it and run `gh auth login`")


def same_tree(a: bytes, b: bytes) -> bool:
    """Equal after dropping the 0 HEAD record (it carries export dates).

    Lines are compared as raw bytes, so a line-ending-only or trailing-newline difference
    counts as a difference: the before commit is made and the diff stays honest.
    """

    def records(data: bytes) -> list[bytes]:
        kept, skipping = [], False
        for line in data.splitlines(keepends=True):
            if line.startswith(b"0 "):
                skipping = line.strip() == b"0 HEAD"
            if not skipping:
                kept.append(line)
        return kept

    return records(a) == records(b)


def _today() -> str:
    return date.today().isoformat()


def _taken(run: Runner, repo: Path, branch: str) -> bool:
    if run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], repo).returncode == 0:
        return True
    return bool(_ok(run, ["git", "ls-remote", "--heads", "origin", branch], repo).strip())


def _write(repo: Path, ged_rel: Path, exports: Exports, written: list[str]) -> None:
    (repo / ged_rel).write_bytes(exports.ged)
    written.append(ged_rel.as_posix())
    if exports.xml is not None:
        xml_rel = ged_rel.with_suffix(".gramps")
        (repo / xml_rel).write_bytes(exports.xml)
        written.append(xml_rel.as_posix())


def _commit(run: Runner, repo: Path, message: str) -> None:
    _ok(run, ["git", "add", "-A"], repo)
    _ok(run, ["git", "commit", "-m", message], repo)


def open_pr(
    repo: Path,
    ged_rel: Path,
    analysis: str,
    before: Exports,
    after: Exports,
    body: str,
    title: str,
    run: Runner = default_runner,
) -> str:
    today = _today()
    base = f"genea/{today}-{analysis}"
    branch, n = base, 1
    while _taken(run, repo, branch):
        n += 1
        branch = f"{base}-{n}"
    # main is checked out and clean, so its file is the work tree file (read as bytes)
    on_main = (repo / ged_rel).read_bytes() if (repo / ged_rel).exists() else None
    _ok(run, ["git", "checkout", "-b", branch], repo)
    written: list[str] = []
    try:
        before_line = "The Gramps Web export before genea matched main, so there is no before commit."
        if on_main is None or not same_tree(before.ged, on_main):
            _write(repo, ged_rel, before, written)
            _commit(run, repo, f"Snapshot {today} before genea")
            before_line = "The first commit holds the Gramps Web state before genea (edits made there since the last export)."
        _write(repo, ged_rel, after, written)
        _commit(run, repo, title)
        _ok(run, ["git", "push", "-u", "origin", branch], repo)
        out = _ok(
            run,
            [
                "gh", "pr", "create", "--base", "main", "--head", branch,
                "--title", title, "--body", f"{body}\n\n{before_line}",
            ],
            repo,
        )
        _ok(run, ["git", "checkout", "main"], repo)
    except Exception as e:
        back = run(["git", "checkout", "-f", "main"], repo)
        # files written by this call that never got committed would make the next check_repo fail
        run(["git", "clean", "-f", "--", *written], repo)
        left = "" if back.returncode == 0 else " (could not switch back to main: run `git checkout -f main`)"
        raise SnapshotError(f"snapshot failed on branch {branch}, kept for you to finish by hand{left}: {e}") from e
    return out.strip().splitlines()[-1]


def _free_branch(run: Runner, repo: Path, base: str) -> str:
    branch, n = base, 1
    while _taken(run, repo, branch):
        n += 1
        branch = f"{base}-{n}"
    return branch


def _back_to_main(run: Runner, repo: Path, written: list[str], branch: str, why: Exception,
                  kept: str) -> SnapshotError:
    back = run(["git", "checkout", "-f", "main"], repo)
    run(["git", "clean", "-f", "--", *written], repo)
    left = "" if back.returncode == 0 else " (could not switch back to main: run `git checkout -f main`)"
    return SnapshotError(f"{kept} {branch}{left}: {why}")


def open_review(
    repo: Path,
    ged_rel: Path,
    analysis: str,
    bundle_rel: Path,
    bundle_text: str,
    diff_text: str,
    title: str,
    body: str,
    run: Runner = default_runner,
) -> tuple[str, str]:
    """Commit the review bundle and the iteration diff on a new branch, open a PR, return (branch, PR url)."""
    branch = _free_branch(run, repo, f"genea/{_today()}-{analysis}-review")
    _ok(run, ["git", "checkout", "-b", branch], repo)
    written: list[str] = []
    try:
        for rel, text in ((bundle_rel, bundle_text), (bundle_rel.with_suffix(".diff"), diff_text)):
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_bytes(text.encode("utf-8"))
            written.append(rel.as_posix())
        _commit(run, repo, title)
        _ok(run, ["git", "push", "-u", "origin", branch], repo)
        out = _ok(run, ["gh", "pr", "create", "--base", "main", "--head", branch, "--title", title,
                        "--body", f"{body}\n\nTree: {ged_rel.as_posix()}"], repo)
        _ok(run, ["git", "checkout", "main"], repo)
    except Exception as e:
        raise _back_to_main(run, repo, written, branch, e, "review PR failed on branch") from e
    return branch, out.strip().splitlines()[-1]


def push_snapshots(
    repo: Path,
    ged_rel: Path,
    branch: str,
    before: Exports,
    after: Exports,
    title: str,
    run: Runner = default_runner,
) -> None:
    """Add the before (if it differs from main) and after snapshot commits to an existing review branch."""
    on_main = (repo / ged_rel).read_bytes() if (repo / ged_rel).exists() else None
    _ok(run, ["git", "fetch", "origin"], repo)
    if run(["git", "checkout", branch], repo).returncode != 0:
        _ok(run, ["git", "checkout", "-b", branch, f"origin/{branch}"], repo)
    written: list[str] = []
    try:
        _ok(run, ["git", "merge", "--ff-only", f"origin/{branch}"], repo)
        if on_main is None or not same_tree(before.ged, on_main):
            _write(repo, ged_rel, before, written)
            _commit(run, repo, f"Snapshot {_today()} before genea")
        _write(repo, ged_rel, after, written)
        _commit(run, repo, title)
        _ok(run, ["git", "push", "origin", branch], repo)
        _ok(run, ["git", "checkout", "main"], repo)
    except Exception as e:
        err = _back_to_main(run, repo, written, branch, e, "snapshot failed on branch")
        run(["git", "branch", "-f", branch, f"origin/{branch}"], repo)  # drop commits that were never pushed
        raise err from e


def read_from_branch(repo: Path, branch: str, rel: str, run: Runner = default_runner) -> str:
    """The text of a file as it is on origin/<branch> now."""
    _ok(run, ["git", "fetch", "origin"], repo)
    return _ok(run, ["git", "show", f"origin/{branch}:{rel}"], repo)
