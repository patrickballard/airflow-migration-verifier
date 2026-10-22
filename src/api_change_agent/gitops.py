"""Branch, commit and push, with a hard guard against pushing to main."""
from __future__ import annotations

import subprocess
from pathlib import Path

PROTECTED = frozenset({"main", "master"})
BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


class UnsafeBranchError(RuntimeError):
    pass


class GitError(RuntimeError):
    pass


def ensure_safe_branch(branch: str, *, default_branch: str | None, base_branch: str | None = None) -> None:
    name = branch.removeprefix("refs/heads/").strip()
    blocked = set(PROTECTED) | {b for b in (default_branch, base_branch) if b}
    if not name or name in blocked:
        raise UnsafeBranchError(
            f"refusing to push to {branch!r}: protected branches are {sorted(blocked)}"
        )


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def publish(repo: Path, *, branch: str, default_branch: str, base_branch: str,
            files: list[str], message: str, remote: str = "origin") -> bool:
    """Commit ``files`` onto ``branch`` (cut from the current HEAD) and push it.

    Returns False when the remote branch already has exactly this tree.
    The working copy is returned to the original ref afterwards.
    """
    ensure_safe_branch(branch, default_branch=default_branch, base_branch=base_branch)
    repo = Path(repo)
    original = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if original == "HEAD":
        original = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-B", branch)
    try:
        _git(repo, "add", "--", *files)
        _git(repo, "-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}",
             "commit", "-q", "--no-verify", "-m", message)

        remote_sha = ""
        listing = _git(repo, "ls-remote", "--heads", remote, f"refs/heads/{branch}")
        if listing:
            remote_sha = listing.split()[0]
            _git(repo, "fetch", "-q", remote, f"refs/heads/{branch}")
            if _git(repo, "rev-parse", "HEAD^{tree}") == _git(repo, "rev-parse", f"{remote_sha}^{{tree}}"):
                return False

        ensure_safe_branch(branch, default_branch=default_branch, base_branch=base_branch)
        _git(repo, "push", "-q", f"--force-with-lease=refs/heads/{branch}:{remote_sha}",
             remote, f"HEAD:refs/heads/{branch}")
        return True
    finally:
        _git(repo, "checkout", "-q", "-f", original, check=False)
