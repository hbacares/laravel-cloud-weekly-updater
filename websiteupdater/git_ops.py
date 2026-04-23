"""Thin wrapper around the local `git` binary.

Git is assumed to be installed and already authenticated (SSH agent or HTTPS
credential helper). We never touch credentials.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(cmd: list[str], cwd: Path | str | None = None, check: bool = True) -> RunResult:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        raise GitError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return RunResult(proc.returncode, proc.stdout, proc.stderr)


def clone_or_fetch(repo_url: str, local_path: Path, main_branch: str = "main") -> None:
    """Ensure `local_path` is an up-to-date, clean checkout of `main_branch`.

    Refuses to proceed if there are uncommitted local changes (protects WIP).
    """
    local_path = Path(local_path)

    if not local_path.exists():
        local_path.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", repo_url, str(local_path)])
        run(["git", "checkout", main_branch], cwd=local_path)
        return

    if not (local_path / ".git").exists():
        raise GitError(f"{local_path} exists but is not a git repository")

    # Dirty-tree guard
    status = run(["git", "status", "--porcelain"], cwd=local_path)
    if status.stdout.strip():
        raise GitError(
            f"Working tree at {local_path} has uncommitted changes; refusing to reset.\n"
            f"{status.stdout}"
        )

    run(["git", "fetch", "origin", "--prune"], cwd=local_path)
    run(["git", "checkout", main_branch], cwd=local_path)
    run(["git", "reset", "--hard", f"origin/{main_branch}"], cwd=local_path)
    run(["git", "clean", "-fd"], cwd=local_path)


def recreate_branch(local_path: Path, branch: str, base: str = "main") -> None:
    """Force-recreate `branch` from the current tip of `base`."""
    run(["git", "checkout", base], cwd=local_path)
    run(["git", "checkout", "-B", branch], cwd=local_path)


def commit_all(local_path: Path, message: str, paths: list[str] | None = None) -> str | None:
    """Stage + commit. Returns the new SHA, or None if there was nothing to commit."""
    if paths:
        for p in paths:
            run(["git", "add", p], cwd=local_path, check=False)
    else:
        run(["git", "add", "-A"], cwd=local_path)

    diff = run(["git", "diff", "--cached", "--name-only"], cwd=local_path)
    if not diff.stdout.strip():
        return None

    run(["git", "commit", "-m", message], cwd=local_path)
    sha = run(["git", "rev-parse", "HEAD"], cwd=local_path).stdout.strip()
    return sha


def force_push(local_path: Path, branch: str, remote: str = "origin") -> None:
    run(["git", "push", "-f", remote, branch], cwd=local_path)


def delete_remote_branch(local_path: Path, branch: str, remote: str = "origin") -> None:
    run(["git", "push", remote, "--delete", branch], cwd=local_path, check=False)


def delete_local_branch(local_path: Path, branch: str, main_branch: str = "main") -> None:
    """Delete local branch if it exists. Switches to main_branch first."""
    # Check if branch exists locally
    result = run(["git", "rev-parse", "--verify", branch], cwd=local_path, check=False)
    if result.ok:
        # Switch to main first to avoid deleting current branch
        run(["git", "checkout", main_branch], cwd=local_path, check=False)
        run(["git", "branch", "-D", branch], cwd=local_path, check=False)


def cleanup_branch(local_path: Path, branch: str, main_branch: str = "main", remote: str = "origin") -> None:
    """Delete branch both locally and remotely if it exists."""
    delete_local_branch(local_path, branch, main_branch)
    delete_remote_branch(local_path, branch, remote)


def merge_into_main(local_path: Path, branch: str, main_branch: str = "main",
                    message: str | None = None) -> str:
    """Merge `branch` into `main_branch` with a merge commit and return the merge SHA."""
    run(["git", "checkout", main_branch], cwd=local_path)
    msg = message or f"autoupdate: merge {branch}"
    run(["git", "merge", "--no-ff", branch, "-m", msg], cwd=local_path)
    sha = run(["git", "rev-parse", "HEAD"], cwd=local_path).stdout.strip()
    run(["git", "push", "origin", main_branch], cwd=local_path)
    return sha


def lockfile_diff_summary(local_path: Path, base: str = "main") -> str:
    """A human-readable summary of what changed in composer.lock / package-lock.json."""
    lines: list[str] = []
    for lock in ("composer.lock", "package-lock.json"):
        r = run(["git", "diff", "--stat", base, "--", lock], cwd=local_path, check=False)
        if r.ok and r.stdout.strip():
            lines.append(f"--- {lock} ---")
            lines.append(r.stdout.strip())
    return "\n".join(lines) if lines else "(no lockfile changes detected)"
