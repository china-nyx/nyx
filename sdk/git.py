"""Git wrapper — repo operations (dirty, commit, tag)."""
import subprocess
from pathlib import Path
from typing import List


class Git:
    def __init__(self, repo: Path):
        self.repo = repo

    def _run(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", self.repo, *args],
                              capture_output=True, text=True)

    def short(self) -> str:
        r = self._run("rev-parse", "--short", "HEAD")
        if r.returncode != 0:
            raise RuntimeError(f"git rev-parse --short HEAD failed: {r.stderr.strip()}")
        return r.stdout.strip()

    def dirty(self) -> bool:
        return bool(self._run("status", "--porcelain").stdout.strip())

    def commit(self, message: str) -> bool:
        self._run("add", "-A")
        if not self.dirty():
            if self._run("diff", "--cached", "--quiet").returncode == 0:
                return True
        return self._run("commit", "-m", message).returncode == 0

    def tag(self, name: str):
        self._run("tag", "-f", name)

    def log(self, n: int = 10) -> List[str]:
        return self._run("log", "--oneline", "-n", str(n)).stdout.strip().splitlines()
