"""Git wrapper — every evolution generation is a commit/tag; the full lineage is traceable and rollback-able."""
import os
import subprocess
from pathlib import Path
from typing import List, Optional

from core import config
from core.log import get_logger
logger = get_logger(__name__)


class Git:
    def __init__(self, repo: str = None):
        self.repo = str(repo or config.REPO)

    def _run(self, *args, cwd=None) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(cwd or self.repo), *args],
                              capture_output=True, text=True)

    def ensure_repo(self):
        if not (Path(self.repo) / ".git").exists():
            self._run("init")
            self._run("config", "user.email", "nyx@local")
            self._run("config", "user.name", "nyx")
            self.commit_all("gen0: genesis")

    def head(self) -> Optional[str]:
        r = self._run("rev-parse", "HEAD")
        return r.stdout.strip() if r.returncode == 0 else None

    def short_branch(self) -> Optional[str]:
        """Return the current branch name, or None if detached HEAD."""
        r = self._run("rev-parse", "--abbrev-ref", "HEAD")
        return r.stdout.strip() if r.returncode == 0 else None

    def short(self) -> str:
        return (self.head() or "unknown")[:8]

    def dirty(self, cwd=None) -> bool:
        return bool(self._run("status", "--porcelain", cwd=cwd).stdout.strip())

    def commit_all(self, message: str, cwd=None) -> bool:
        self._run("add", "-A", cwd=cwd)
        if not self.dirty(cwd=cwd):
            # May already be staged but with no worktree changes; still attempt to commit
            if self._run("diff", "--cached", "--quiet", cwd=cwd).returncode == 0:
                return True
        return self._run("commit", "-m", message, cwd=cwd).returncode == 0

    def tag(self, name: str):
        self._run("tag", "-f", name)

    def has_ref(self, ref: str) -> bool:
        return self._run("rev-parse", "--verify", "--quiet", ref).returncode == 0

    def reset_hard(self, ref: str):
        self._run("reset", "--hard", ref)
        self._run("clean", "-fd")  # .gitignore protects .venv/data directories

    def reset_hard_at(self, worktree: str, ref: str):
        self._run("reset", "--hard", ref, cwd=worktree)
        self._run("clean", "-fd", cwd=worktree)

    # ---- worktree (candidate-generation isolation) ----
    def add_worktree(self, path: str, branch: str, base: str) -> bool:
        # Delete any old branch/worktree with the same name
        self._run("worktree", "remove", "--force", path)
        self._run("branch", "-D", branch)
        return self._run("worktree", "add", "-f", "-b", branch, path, base).returncode == 0

    def add_worktree_detached(self, path: str, base: str = "HEAD") -> bool:
        self._run("worktree", "remove", "--force", path)
        return self._run("worktree", "add", "-f", "--detach", path, base).returncode == 0

    def remove_worktree(self, path: str, branch: str = None):
        self._run("worktree", "remove", "--force", path)
        if branch:
            self._run("branch", "-D", branch)

    # ---- promote helpers ----
    def update_ref(self, ref: str, sha: str):
        """Move a ref to the given SHA."""
        self._run("update-ref", ref, sha)

    def rev_parse(self, rev: str, cwd: str = None) -> str:
        """Return the full SHA for a revision."""
        return self._run("rev-parse", rev, cwd=cwd).stdout.strip()

    def rev_parse_short(self, rev: str, cwd: str = None) -> str:
        """Return the short SHA for a revision."""
        return self._run("rev-parse", "--short", rev, cwd=cwd).stdout.strip()

    def cleanup_stale(self):
        """Remove leftover candidate worktrees, stale upgrade branches/tags, and revert uncommitted edits
        to the running source (app/, core/) left by a killed/aborted generation.
        Portable (runs at boot) so no external stop hook is needed."""
        for ln in self._run("worktree", "list").stdout.splitlines()[1:]:   # skip the main worktree (first line)
            parts = ln.split()
            if parts:
                self._run("worktree", "remove", "--force", parts[0])
        self._run("worktree", "prune")
        # Delete stale upgrade/self-upgrade branches (left by aborted generations)
        r = self._run("branch", "--list", "upgrade-*", "self-upgrade-*")
        for line in r.stdout.splitlines():
            name = line.strip().lstrip("* ")
            if name:
                self._run("branch", "-D", name)
        # Delete stale upgrade/self-upgrade tags (left by aborted generations)
        r = self._run("tag", "-l", "upgrade-*", "self-upgrade-*")
        for line in r.stdout.splitlines():
            tag_name = line.strip()
            if tag_name:
                self._run("tag", "-d", tag_name)
        # Delete ALL stale gen-* tags (legacy, no longer needed)
        r = self._run("tag", "-l", "gen-*")
        for line in r.stdout.splitlines():
            tag_name = line.strip()
            if tag_name:
                self._run("tag", "-d", tag_name)
        # Delete other known stale tags
        for stale_tag in ["kernel-good"]:
            if self._run("rev-parse", "--verify", "--quiet", stale_tag).returncode == 0:
                self._run("tag", "-d", stale_tag)
        self._run("checkout", "--", "app", "core")
        self._run("clean", "-fdq", "app", "core")

    def ff_merge(self, branch: str) -> bool:
        """Promote the candidate into master. Prefer fast-forward; if master has moved (ff impossible,
        e.g. a concurrent/manual commit), fall back to a regular merge so the whole generation isn't
        discarded; abort cleanly only on real conflict."""
        if self._run("merge", "--ff-only", branch).returncode == 0:
            return True
        if self._run("merge", "--no-edit", branch).returncode == 0:
            return True
        self._run("merge", "--abort")
        return False

    def log(self, n: int = 10) -> List[str]:
        return self._run("log", "--oneline", "-n", str(n)).stdout.strip().splitlines()
