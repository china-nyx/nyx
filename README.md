# NYX — a self-evolving autonomous agent

NYX is a self-evolving autonomous agent. You give it a goal; it tries to solve it with
its tools, and when it hits a capability it lacks, it **rewrites its own code** to gain
that capability — behind a safety gate that can always roll the system back to a known-good
state. Left running, it improves itself continuously and grounded against real reference
projects, without a human in the loop.

## How it works

```
requirement ─▶ solver (tries with current tools + skills)
                 ├─ solved  ─▶ done
                 └─ needs_upgrade ─▶ evolver: edit in worktree → smoke → promote → restart

crash ─▶ self-heal: capture traceback → evolver fixes code → promote → restart
```

- **Solver** attempts the task with 4 base tools (`bash`, `read`, `write`, `edit`) and
  skills loaded from `$NYX_HOME/skills/`. Returns structured JSON: `done` or `needs_upgrade`.
- **Evolver** edits code in a throwaway git worktree, promotes only if it passes a smoke check.
  The system controls the promote decision (FSM, not a tool call).
- **Boot self-check + rollback**: every start verifies health;
  if anything regressed, it hard-rolls-back to the last good version.
- **Self-heal**: runtime crashes are caught, the full traceback is fed to the evolver as a
  repair task. On success NYX reboots from the fixed code (max 3 consecutive attempts).

## Architecture

### Source Repository

```
core/       — boot, gate, git, recovery, config, log
app/        — agent, solver, evolver, scheduler
sdk/        — tools.py (4 base tools), llm.py, atomic_io, exceptions
skills/     — built-in skills (loaded at runtime from source repo)
```

### Runtime State (`$NYX_HOME/`)

```
task/       — per-task persistent state (scheduler managed)
              ├── active            active (non-done) tids, scheduler only scans these
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

- Promotion requires passing the smoke gate; the boot self-check + rollback catch regressions.
- The evolver FSM controls all code changes — the LLM cannot bypass the safety gate.
- Source repo is bind-mounted read-only at boot, preventing accidental writes by the solver.
- **Self-heal**: if NYX crashes at runtime, it catches the exception, feeds the full traceback to the evolver, and reboots from the fixed version — up to 3 consecutive attempts before giving up.

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
    },
    "boot": {
        "max_recover": 2
    }
}
```

See `core/config.py` for all keys and env var overrides.
