"""Upgrader — editor session in a temporary worktree → smoke → promote → restart.

No planner, no recursion. The editor reads the requirement and source code,
figures out what to change, and implements it.

If the editor needs further upgrades, it creates a child task (priority 99).
The parent task goes to upgrade-waiting state. After restart, the scheduler
picks up the child task first, then resumes the parent.

Flow:
  create worktree → editor(read/write/edit worktree) → smoke → promote → restart
"""
import glob
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core import config
from core.git import Git
from core.log import get_logger
from sdk.tools import format_tool_log

logger = get_logger(__name__)

SMOKE_FIX_RETRIES = 3


# ── System prompts ───────────────────────────────────────────────────

EDITOR_PROMPT_TEMPLATE = """\
You are NYX's upgrade editor. Read the requirement, study the source code, and implement the needed changes.

## Requirement
{requirement}

{smoke_error}

## Source Code (editable)
This is a git worktree at: {worktree}/
Read the current code here to understand what needs to change, then make the edits.

## Tools
- bash — run smoke checks (import all modules) in the worktree
- read, write, edit — modify source code in the worktree

## Response
First summarize what you changed, then list changes one line per file."""


# ── Worktree management ──────────────────────────────────────────────

def _create_worktree() -> str:
    """Create a temporary detached worktree. Returns wt_path."""
    ts = int(time.time())
    wt_path = config.WORKTREES / f"upgrade-{ts}"
    config.WORKTREES.mkdir(parents=True, exist_ok=True)

    code = config.CODE
    # Unlock source repo so git can create refs for the worktree
    subprocess.call(["umount", "-l", str(code)], stderr=subprocess.DEVNULL)
    from core.git import Git
    current_branch = Git().short_branch() or "HEAD"
    if not Git().add_worktree_detached(str(wt_path), current_branch):
        raise RuntimeError(f"failed to create worktree: {wt_path}")
    logger.info(f"[upgrade] worktree created: {wt_path} (detached)")
    return str(wt_path)


def _remove_worktree(wt_path: str) -> None:
    from core.git import Git
    Git().remove_worktree(wt_path)
    if Path(wt_path).exists():
        shutil.rmtree(wt_path, ignore_errors=True)


# ── Smoke check ──────────────────────────────────────────────────────

def smoke_check(wt_path: str) -> Tuple[bool, str]:
    if not Path(wt_path).is_dir():
        return False, f"worktree not found: {wt_path}"

    old_path = list(sys.path)
    mods: List[str] = []
    try:
        sys.path.insert(0, wt_path)
        dirs = ['core', 'sdk', 'app']
        skip = {'__init__.py', '__main__.py', 'boot.py'}
        for d in dirs:
            p = os.path.join(wt_path, d)
            if os.path.isdir(p):
                for f in glob.glob(os.path.join(p, '*.py')):
                    base = f.rsplit('/', 1)[-1]
                    if base not in skip:
                        rel = os.path.relpath(f, wt_path)
                        mods.append(rel[:-3].replace(os.sep, '.'))
        for m in sorted(mods):
            importlib.import_module(m)
        return True, "Smoke check PASSED"
    except Exception as e:
        return False, f"Smoke check FAILED: {e}"
    finally:
        sys.path[:] = old_path
        for m in sorted(mods, reverse=True):
            sys.modules.pop(m, None)


# ── Promote ───────────────────────────────────────────────────────────


def _promote(*, message: str, wt_path: str, tid: str = "") -> None:
    """Commit worktree → merge → tag → re-exec. Never returns on success.

    Skills are deployed by boot.py on the next start.

    message: editor content — written to parent task's note.md as upgrade feedback.
    tid: the upgrade task id — marked done before re-exec so scheduler doesn't re-pick it."""
    code = config.CODE
    home = config.HOME
    git = Git(str(code))
    branch = git.short_branch() or "HEAD"

    log_lines: List[str] = []
    def log(msg: str):
        log_lines.append(msg)
        logger.info(f"[upgrade] {msg}")

    if not git.dirty(cwd=wt_path):
        log("No changes in worktree — nothing to promote")
        # Clean up the worktree and mark task done without re-exec.
        _remove_worktree(wt_path)
        if tid:
            from app import scheduler
            scheduler.mark_done(tid, message or "upgrade complete (no changes)")
            parent_tid = (home / "task" / tid / "parent_tid").read_text().strip()
            if parent_tid:
                scheduler.set_state(parent_tid, "running")
                nf = home / "task" / parent_tid / "note.md"
                old_note = ""
                if nf.exists():
                    try:
                        old_note = nf.read_text(encoding="utf-8").strip()
                    except Exception:
                        pass
                child_note = f"# UPGRADE DONE\n{message}"
                combined = f"{old_note}\n\n{child_note}".strip() if old_note else child_note
                nf.write_text(combined, encoding="utf-8")
                log(f"[done] restored parent {parent_tid} to running")
        return

    msg = message or "upgrade: code change"

    git.commit_all(f"nyx: {msg}", cwd=wt_path)
    sha_short = git.rev_parse_short("HEAD", cwd=wt_path)
    log(f"[1/5] committed: {sha_short} — {msg}")

    # Unmount read-only lock so we can update refs in the main repo
    subprocess.call(["umount", "-l", str(code)], stderr=subprocess.DEVNULL)
    time.sleep(0.1)
    log("[2/5] unlocked source repo")

    # Get the new commit SHA from the worktree and move main to it
    wt_head = git.rev_parse("HEAD", cwd=wt_path)
    git.update_ref(f"refs/heads/{branch}", wt_head)
    log("[3/5] updated main to worktree commit")

    # Tag with safe-boot
    git.tag("safe-boot")
    log("[4/4] tagged: safe-boot")

    # Re-lock source repo before re-exec (boot.py will also mount ro)
    subprocess.check_call(["mount", "--bind", "-o", "ro", str(code), str(code)])
    log("[done] locked source repo")

    _remove_worktree(wt_path)

    # Mark this upgrade task done and restore its parent before re-exec.
    # Without this, the child stays in state 'new' and scheduler re-picks it → infinite loop.
    if tid:
        from app import scheduler
        scheduler.mark_done(tid, message or "upgrade complete")
        parent_tid = (home / "task" / tid / "parent_tid").read_text().strip()
        if parent_tid:
            scheduler.set_state(parent_tid, "running")
            # Append editor's content as feedback for parent solver
            nf = home / "task" / parent_tid / "note.md"
            old_note = ""
            if nf.exists():
                try:
                    old_note = nf.read_text(encoding="utf-8").strip()
                except Exception:
                    pass
            child_note = f"# UPGRADE DONE\n{message}"
            combined = f"{old_note}\n\n{child_note}".strip() if old_note else child_note
            nf.write_text(combined, encoding="utf-8")
            log(f"[done] restored parent {parent_tid} to running")

    log("Re-execing NYX...")
    boot_py = code / "core" / "boot.py"
    os.execv(sys.executable, [sys.executable, str(boot_py)])


# ── LLM helpers ──────────────────────────────────────────────────────

def _parse_json_response(text: str) -> Optional[Dict]:
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    m = re.search(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            pass
    brace_start = text.find('{')
    if brace_start >= 0:
        try:
            data = json.loads(text[brace_start:])
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            pass
    return None


# ── Triage ───────────────────────────────────────────────────────────


# ── Editor tools ─────────────────────────────────────────────────────

_EDITOR_TOOLS = [
    {"type": "function", "function": {
        "name": "bash",
        "description": "Execute a shell command in the worktree. Use for running smoke checks, imports, tests.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "The shell command to run."},
            "timeout": {"type": "integer", "description": "Seconds before kill (default 120)."}},
        "required": ["command"]}}},

    {"type": "function", "function": {
        "name": "read",
        "description": "Read a file from the worktree.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"}},
        "required": ["path"]}}},

    {"type": "function", "function": {
        "name": "write",
        "description": "Write content to a file in the worktree.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}},
        "required": ["path", "content"]}}},

    {"type": "function", "function": {
        "name": "edit",
        "description": "Replace exact old_text with new_text in a file (first match only).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"}},
        "required": ["path", "old_text", "new_text"]}}},
]


def _editor_session(llm, executor, requirement: str, worktree: str,
                    tid: str = "", smoke_error: Optional[str] = None) -> Dict:
    """Editor session: read requirement + source code, implement changes."""
    # Session log per task
    import subprocess as _sub
    try:
        _ver = _sub.run(["git", "-C", str(config.CODE), "rev-parse", "--short", "HEAD"],
                        capture_output=True, text=True).stdout.strip()[:8]
    except Exception:
        _ver = "unknown"
    sess_dir = config.TASK_DIR / (tid or "adhoc") / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    sess = sess_dir / f"editor-{_ver}.jsonl"

    smoke_section = ""
    if smoke_error:
        smoke_section = f"## Previous smoke check failed:\n{smoke_error}\nFix the errors.\n\n"

    system_prompt = EDITOR_PROMPT_TEMPLATE.format(
        requirement=requirement,
        smoke_error=smoke_section,
        worktree=worktree,
    )

    user_prompt = (
        f"Read the source code in the worktree at {worktree}/\n"
        f"Analyze what needs to change, implement it, and describe what you did."
    )

    _step_num = 0

    def _on_step(name, args, res_, err):
        nonlocal _step_num
        _step_num += 1
        logger.info(format_tool_log("editor", tid, _step_num, name, args, res_, err, 0))
        try:
            rec = {"type": "tool", "tool": name, "step": _step_num,
                   "args": {k: str(v)[:200] for k, v in (args or {}).items()},
                   "ok": (not err), "result": str(res_)[:1000]}
            with open(sess, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    res = llm.run_agent(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tool_executor=executor,
        tools=_EDITOR_TOOLS,
        temperature=0.5,
        on_step=_on_step,
    )

    return {"status": "done", "content": (res.get("content") or "").strip()}


# ── Main entry point ─────────────────────────────────────────────────

def run(llm, executor, *, requirement: str, tid: str = "") -> None:
    """Run the upgrade pipeline. Linear: editor → smoke → promote → restart.

    On success: calls os.execv (never returns).
    On failure: raises UpgradeFailed.
    """
    from sdk.exceptions import UpgradeFailed

    logger.info(f"[upgrade] [tid {tid}] starting upgrade")

    wt_path = _create_worktree()
    try:
        # ── Editor session ─────────────────────────────────────────
        editor_result = _editor_session(llm, executor, requirement, wt_path, tid=tid)

        # ── Smoke check with fix retries ───────────────────────────
        for attempt in range(1, SMOKE_FIX_RETRIES + 1):
            ok, detail = smoke_check(wt_path)
            if ok:
                break
            logger.warning(f"[upgrade] [tid {tid}] smoke failed (attempt {attempt}): {detail}")
            editor_result = _editor_session(
                llm, executor, requirement, wt_path, tid=tid, smoke_error=detail)
        else:
            raise UpgradeFailed(f"smoke check failed after {SMOKE_FIX_RETRIES} attempts: {detail}")

        # ── Promote and restart ────────────────────────────────────
        content = editor_result.get("content", "upgrade")
        _promote(message=content, wt_path=wt_path, tid=tid)

    except Exception:
        _remove_worktree(wt_path)
        raise


