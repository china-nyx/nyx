# NYX — a self-evolving autonomous agent

NYX is a self-evolving autonomous agent. You give it a goal; it tries to solve it with
its tools, and when it hits a capability it lacks, it **rewrites its own code** to gain
that capability. Left running, it improves itself continuously and grounded against real reference
projects, without a human in the loop.

## How it works

```
requirement ─▶ solver (tries with current tools + skills)
                 ├─ solved  ─▶ done
                 └─ needs_upgrade ─▶ evolver: editor edits worktree → promote → restart

crash ─▶ boot catches exception → evolver (editor → promote → restart)
```

- **Solver** attempts the task with 4 base tools (`bash`, `read`, `write`, `edit`) and
  skills loaded from `$NYX_HOME/skills/`. Returns structured JSON: `done` or `needs_upgrade`.
- **Editor** is the stable core — an LLM agent session in a throwaway git worktree.
  It reads requirements, studies source code, and implements changes. Only depends on core/ + sdk/.
- **Evolver** orchestrates the editor: creates a worktree, runs the editor, promotes changes to main, restarts.
- **Boot** starts the agent. If anything fails (import error, crash), boot invokes evolver to fix the code.

## Architecture

### Source Repository

```
core/       — boot, git, config, log
app/        — agent, editor, evolver, solver, scheduler
sdk/        — tools.py (4 base tools), llm.py, atomic_io, exceptions
skills/     — built-in skills (loaded at runtime from source repo)
```

### Runtime State (`$NYX_HOME/`)

```
task/       — per-task persistent state (scheduler managed)
              ├── active            active (non-done) tids, scheduler only scans these
              ├── current_tid       tid of the task currently being executed
              ├── index.md          human-readable history (all tasks including done)
              └── <tid>/            state, priority, requirement.md, note.md, result.md
skills/     — runtime skills (override built-in by name)
mailbox/    — inbox/ only (requirements ingested to task/, files deleted after ingestion)
worktree/   — temporary git worktrees (created on-demand by evolver, deleted after promote)
sandbox/    — your work area (projects, research, data — put everything here)
├── src → CODE              symlink to source repo (bind-mounted read-only)
```

### OS Process Model

NYX manages requirements as tasks with an OS-like scheduler:

- Each requirement becomes a **task** with its own directory (`task/<tid>/`)
- Tasks have states: `new` → `running` → `done`, or `running` → `upgrade-waiting` → `running`
- The scheduler picks the next task by priority (99 = upgrade preemption)
- When a task needs code changes, it spawns a child upgrade task (priority 99) and waits
- After restart, child tasks resume first, then parents

### Skills

- **4 base tools** (`bash`, `read`, `write`, `edit`) are the only code-level capabilities.
- **Built-in skills** live in the source repo (`skills/`) and are loaded directly from there.
  They cover generic agent behavior like self-reflection and memory management.
- **Runtime skills** go directly in `$NYX_HOME/skills/<name>/SKILL.md`. They override
  built-in skills by name — if a runtime skill has the same name as a built-in one,
  the runtime version is used. This lets you customize or extend behavior without touching code.
- The agent reads a skill's SKILL.md and executes its steps using the base tools.
  No code change needed to add new capabilities.

### Self-Reflection

NYX periodically audits itself — source code, documentation, skills, memory files,
tasks, and even its own self-reflect procedure. Every cycle aims to leave the workspace
in a slightly better state: summarize stale entries, fix drift between docs and reality,
improve skill steps, discover capability gaps.

Self-reflection runs automatically every 3600 seconds (configurable via `NYX_SELF_REFLECT_SEC`).

To customize what self-reflect audits, create `$NYX_HOME/config/self-reflect.md` with your own requirement text. NYX will use it instead of the built-in default.

## Safety model

- The evolver FSM controls all code changes — the LLM cannot bypass the promote gate.
- Source repo is bind-mounted read-only at boot, preventing accidental writes by the solver.
- **Self-heal**: if NYX crashes at any point, boot catches the exception and invokes the editor to fix the code.

## Running it

NYX is pure Python (standard library) managed with [uv](https://github.com/astral-sh/uv),
and talks to any OpenAI-compatible model server (e.g. a local `llama-server`).

```bash
# Create $NYX_HOME/config/settings.json first:
{
    "llm": {
        "base_url": "http://127.0.0.1:8001/v1",
        "model": "your-model",
        "api_key": ""
    }
}

export NYX_HOME=/path/to/nyx/workspace
python3 /path/to/nyx/repo/core/boot.py
```

Or with systemd (recommended for production):

```ini
[Service]
ExecStart=/path/to/nyx/repo/.venv/bin/python3 /path/to/nyx/repo/core/boot.py
WorkingDirectory=/path/to/nyx/workspace
Restart=on-failure
```

> `WorkingDirectory` determines the runtime root. It must not be inside the source repo.

### Sending Tasks

Drop a `.md` file into `$NYX_HOME/mailbox/inbox/`. The scheduler ingests it and creates a task.

**Filename convention:** use `<priority>-<description>.md` (e.g. `90-urgent-fix.md`).
The scheduler parses priority from the filename prefix — larger number = higher priority. Default is 50 if the prefix is not a valid integer.

### Configuration

All runtime config is in `$NYX_HOME/config/settings.json`. Env vars override file values:

```json
{
    "llm": {
        "base_url": "http://127.0.0.1:8001/v1",
        "model": "your-model",
        "api_key": "",
        "timeout": 300
    },
    "sandbox": {
        "timeout": 180,
        "mem_mb": 4096
    },
    "log": {
        "max_mb": 50,
        "keep_sessions": 300
    }
}
```

See `core/config.py` for all keys and env var overrides.
