# NYX — a self-evolving autonomous agent

NYX is a self-evolving autonomous agent. You give it a goal; it tries to solve it with
its tools, and when it hits a capability it lacks, it **rewrites its own code** to gain
that capability. Left running, it improves itself continuously and grounded against real reference
projects, without a human in the loop.

## How it works

```
requirement ─▶ executor.run(solver) ─▶ solves task, modifies repo if needed
                            │
                            ├─ no code changes  ─▶ done
                            └─ code changes     ─▶ commit → os.execv restart

crash ─▶ boot/main catches exception ─▶ self-heal → executor.run(hotfixer) → fix code → restart
```

- **Solver** attempts the task with 4 base tools (`bash`, `read`, `write`, `edit`) and
  skills loaded from `skills/` (under cwd). Can modify repo source directly.
- **Hotfixer** is a mini code-fix agent — 4 tools only, modifies repo source.
  Invoked by self-heal when NYX crashes.
- **Executor** wraps any agent session: records git HEAD before/after the call,
  commits + `os.execv` restarts if repo code changed. This is what enables
  self-evolution — code changes take effect immediately on restart.
- **Boot** starts the agent. If anything fails during startup (import error, crash),
  boot invokes self-heal which calls hotfixer to fix the code.

## Architecture

### Source Repository

```
app/        — boot, config, executor, hotfixer, log, main, prompts,
              scheduler, self_heal, daily_reflect, session, solver
              hooks/ — compaction, duplicate_pruner, repetitive_guard,
                      step_logger, terminal_tool
sdk/        — agent.py (loop), agent_hooks.py (protocol + composition),
              llm.py, tools.py, fs.py, git.py, skills.py, schemas.py
skills/     — built-in skills (loaded at runtime from source repo)
deploy/     — systemd unit template
tests/      — test suite
```

### Runtime State (cwd)

```
task/       — per-task persistent state (scheduler managed)
              ├── active            active (non-done) tids, scheduler only scans these
              ├── current_tid       tid of the task currently being executed
              ├── index.md          human-readable history (all tasks including done)
              └── <tid>/            state, priority, requirement.md, note.md, result.md
skills/     — runtime skills (override built-in by name)
mailbox/    — inbox/ only (requirements ingested to task/, files deleted after ingestion)
projects/   — long-running projects (each gets its own directory)
toolbox/    — shared utilities (multi-project tools only)
temp/       — scratch space (auto-cleaned on restart)
```

### OS Process Model

NYX manages requirements as tasks with an OS-like scheduler:

- Each requirement becomes a **task** with its own directory (`task/<tid>/`)
- Tasks have states: `new` → `running` → `done`
- The scheduler picks the next task by priority
- All agent sessions run through `executor.run()` — if repo code changes, auto-commit + `os.execv` restart

### Skills

- **4 base tools** (`bash`, `read`, `write`, `edit`) are the only code-level capabilities.
- **Built-in skills** live in the source repo (`skills/`) and are loaded directly from there.
  They cover generic agent behavior like daily reflection, post-task reflection, and memory management.
- **Runtime skills** go directly in `skills/<name>/SKILL.md` (under cwd). They override
  built-in skills by name — if a runtime skill has the same name as a built-in one,
  the runtime version is used. This lets you customize or extend behavior without touching code.
- The agent reads a skill's SKILL.md and executes its steps using the base tools.
  No code change needed to add new capabilities.

### Daily Reflection & Post-Task Reflection

NYX has two layers of self-improvement:

**Daily reflection** (`skills/daily-reflect/`) — runs automatically every 24 hours. Performs a deep audit of source code, documentation, skills, memory files, tasks, projects, and toolbox. Creates inbox tasks for actionable improvements.

**Post-task reflection** (`skills/task-reflect/`) — triggered after each completed task. Lightweight: organizes memory and evaluates whether the work should be captured as a reusable skill.

To customize daily reflection, place your own SKILL.md at `skills/daily-reflect/SKILL.md` (under cwd) — it shadows the built-in version.

## Safety model

- Executor controls all code changes — detects repo HEAD changes after agent sessions, commits + `os.execv` restarts.
- **Self-heal**: if NYX crashes at any point, boot catches the exception and invokes the hotfixer to fix the code.

## Running it

NYX is a Python project managed with [uv](https://github.com/astral-sh/uv)
(dependencies: pydantic) and talks to any OpenAI-compatible model server (e.g. a local `llama-server`).

```bash
# Create config/settings.json in your working directory first:
{
    "llm": {
        "base_url": "http://127.0.0.1:8001/v1",
        "model": "your-model",
        "api_key": ""
    }
}

cd /path/to/nyx/workspace
python3 /path/to/nyx/repo/app/boot.py
```

Or with systemd (recommended for production):

```ini
[Service]
ExecStart=/path/to/nyx/repo/.venv/bin/python3 /path/to/nyx/repo/app/boot.py
WorkingDirectory=/path/to/nyx/workspace
Restart=on-failure
```

> `WorkingDirectory` determines the runtime root. It must not be inside the source repo.

### Sending Tasks

Drop a `.md` file into `mailbox/inbox/`. The scheduler ingests it and creates a task.

**Filename convention:** use `<priority>-<description>.md` (e.g. `90-urgent-fix.md`).
The scheduler parses priority from the filename prefix — larger number = higher priority. Default is 50 if the prefix is not a valid integer.

### Configuration

All runtime config is in `config/settings.json`. Env vars override file values:

```json
{
    "llm": {
        "base_url": "http://127.0.0.1:8001/v1",
        "model": "your-model",
        "api_key": "",
        "timeout": 300
    },
    "log": {
        "keep_days": 7
    },
    "session": {
        "keep_sessions": 300
    },
    "compaction": {
        "enabled": true,
        "reserve_tokens": 16384,

    }
}
```

The `compaction` section controls context-window compaction behaviour. All keys are optional — the defaults shown above will be used when the section is omitted.

### Environment Variable Overrides

The following settings can be overridden via environment variables:

| Env Var | Setting | Default |
|---------|---------|----------|
| `NYX_REQ_RETRY_SEC` | seconds between retry attempts for same task | 25 |
| `NYX_DAILY_REFLECT_SEC` | seconds between daily reflection cycles | 86400 |

See `app/config.py` for all keys and their defaults.
