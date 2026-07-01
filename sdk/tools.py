"""Core-owned tool executor (file/shell), rooted at a working dir.

Owned by the core (not the app). The solver agent uses it for task-solving;
code upgrades are handled by the evolver FSM phase (app/evolver.py).

4 base tools: bash, read, write, edit — everything else is a skill.
"""
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Tuple

from core import config
from sdk.atomic_io import atomic_write_text


class Tools:
    def __init__(self, cwd=None):
        self.cwd = Path(cwd) if cwd else config.HOME
        self.llm = None

    def execute(self, name: str, args: Dict) -> Tuple[str, bool]:
        try:
            fn = getattr(self, f"_t_{name}", None)
            if not fn:
                return f"unknown tool: {name}", True
            return fn(args)
        except Exception as e:
            return f"{type(e).__name__}: {e}", True

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (self.cwd / p)

    # Wall-clock ceiling for bash.
    BASH_WALL_MAX = 999999

    def _t_bash(self, args) -> Tuple[str, bool]:
        command = args.get("command", "")
        env = os.environ.copy()
        # Prepend .venv/bin to PATH so `python3` resolves to the project's Python 3.12
        _venv_bin = str(Path(sys.executable).parent)
        env["PATH"] = f"{_venv_bin}:{env.get('PATH', '')}"
        # Honor caller-supplied timeout (default 120s), clamped to BASH_WALL_MAX ceiling.
        try:
            wall_timeout = min(int(args.get("timeout", 120)), self.BASH_WALL_MAX)
        except Exception:
            wall_timeout = 120
        try:
            r = subprocess.run(command, shell=True, capture_output=True,
                               text=True, timeout=wall_timeout, cwd=str(self.cwd), env=env)
        except subprocess.TimeoutExpired:
            return f"timeout after {wall_timeout}s", True
        out = (r.stdout if r.returncode == 0 else (r.stderr or r.stdout))[:65536]
        return out, r.returncode != 0

    def _t_read(self, args) -> Tuple[str, bool]:
        p = self._resolve(args.get("path", ""))
        if not p.exists():
            return f"not found: {p}", True
        if p.is_dir():
            return f"{p} is a directory; use ls", True
        text = p.read_text(encoding="utf-8", errors="replace")
        # Slice big files by line: offset (start line, 1-based) + limit (number of lines); defaults to reading the whole file (capped at 64KB)
        off = int(args.get("offset", 0) or 0)
        lim = int(args.get("limit", 0) or 0)
        if off or lim:
            lines = text.splitlines()
            start = max(0, off - 1) if off else 0
            end = (start + lim) if lim else len(lines)
            sel = lines[start:end]
            return (f"[lines {start+1}-{start+len(sel)} of {len(lines)}]\n" + "\n".join(sel))[:65536], False
        return text[:65536], False


    def _t_write(self, args) -> Tuple[str, bool]:
        path = args.get("path", "")
        if not path:
            return "write: 'path' is required", True
        p = self._resolve(path)
        if p.exists() and p.is_dir():
            return f"cannot write to a directory: {p}", True
        atomic_write_text(p, args.get("content", ""))
        return f"written {p}", False

    def _t_edit(self, args) -> Tuple[str, bool]:
        p = self._resolve(args.get("path", ""))
        if not p.exists():
            return f"not found: {p}", True
        content = p.read_text(encoding="utf-8", errors="replace")
        old = args.get("old_text", "")
        if old not in content:
            return "old_text not found", True
        atomic_write_text(p, content.replace(old, args.get("new_text", ""), 1))
        return "edited", False

# ── Shared tool-call logging helpers (used by solver) ───────────────────────

def _tool_brief(name, args, show_full_path=False):
    """One-line brief summary of tool args for logging."""
    args = args or {}
    if name == "bash":
        return (args.get("cmd") or args.get("command") or "").strip().replace("\n", " ")[:512]
    if name == "http_get":
        return str(args.get("url") or "")[:120]
    path = args.get("path") or args.get("file") or args.get("filename")
    pat = args.get("pattern") or args.get("query")
    if path and pat:
        display_path = str(path)
        return f"{pat!r} in {display_path}"
    if path:
        return str(path)
    if pat:
        return str(pat)[:80]
    return ", ".join(f"{k}={str(v)[:30]}" for k, v in list(args.items())[:2])[:100]


def _result_summary(res, err):
    """Short result summary for inline tool-call log."""
    import re
    if not res:
        return ""
    s = str(res).strip()
    while s and s[0] in ('"', "'", "`"):
        s = s[1:]
    lines = [l.strip() for l in s.splitlines() if l.strip()]
    if not lines:
        return ""
    for line in lines:
        m = re.search(r'exit\s+(\d+)', line)
        if m:
            return f"exit {m.group(1)}"
    if err:
        return lines[0][:60]
    total_len = len(s)
    if total_len < 200:
        return lines[0]
    if len(lines) > 1:
        return f"{len(lines)} lines"
    return ""


def format_tool_log(role, context, step_num, name, args, res, err, duration, *, context2=None):
    """Format a single-line unified tool-call log entry."""
    status = "✗" if err else "✓"
    brief = _tool_brief(name, args, show_full_path=err)
    ctx = f"[{context}]"
    if context2:
        ctx += f" [{context2}]"
    parts = [f"{ctx} · step {step_num}: {name} {status} ({duration:.1f}s) — {brief}"]
    summary = _result_summary(res, err)
    if summary:
        parts.append(f"→ {summary}")
    return " ".join(parts)


# ── Tool schemas (4 base tools; everything else is a skill) ───────────
# Code upgrades are handled by the evolver FSM phase.

_ALL_TOOL_DEFS = [
    {"type": "function", "function": {
        "name": "bash",
        "description": "Execute a shell command on the host. Use for running code, processing data, "
                       "calling system tools, installing packages, network access. Returns exit code + stdout + stderr.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "The shell command to run."},
            "timeout": {"type": "integer", "description": "Seconds before kill (default 120)."}},
            "required": ["command"]}}},

    {"type": "function", "function": {
        "name": "read",
        "description": "Read a file. Optional offset (1-based start line) + limit (number of lines) to read a slice.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}},
            "required": ["path"]}}},

    {"type": "function", "function": {
        "name": "write",
        "description": "Write content to a file. Creates the file if it doesn't exist, overwrites if it does. Automatically creates parent directories.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]}}},

    {"type": "function", "function": {
        "name": "edit",
        "description": "Replace exact old_text with new_text in a file (first match only).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
            "required": ["path", "old_text", "new_text"]}}},

]

ALL_TOOLS = list(_ALL_TOOL_DEFS)  # mutable-safe copy for callers
ALL_TOOL_NAMES = frozenset(d["function"]["name"] for d in _ALL_TOOL_DEFS)
