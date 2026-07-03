# NYX — a self-evolving autonomous agent

NYX is a self-evolving autonomous agent. You give it a goal; it tries to solve it with
its tools, and when it hits a capability it lacks, it **rewrites its own code** to gain
that capability. Left running, it improves itself continuously and grounded against real reference
projects, without a human in the loop.

## How it works

```
requirement ─▶ evolve(solver) ─▶ solves task, modifies repo if needed
                     │
                     ├─ no code changes  ─▶ done
                     └─ code changes     ─▶ commit → restart

crash ─▶ boot catches exception ─▶ evolve(hotfixer) → fix code → restart
```

- **Solver** attempts the task with 4 base tools (`bash`, `read`, `write`, `edit`) and
  skills loaded from `skills/` (under cwd). Can modify repo source directly.
- **Hotfixer** is a mini code-fix agent — 4 tools only, modifies repo source.
  Used by boot self-heal for crash recovery.
- **Evolver** wraps any agent session: records git HEAD before/after, commits + restarts if code changed.
- **Boot** starts the agent. If anything fails (import error, crash), boot invokes hotfixer to fix the code.

## Architecture

### Source Repository

```
app/        — boot, config, executor, hotfixer, log, main, prompts,
              scheduler, self_heal, self_reflect, session, solver
sdk/        — agent.py (loop), compaction.py, llm.py, tools.py, fs.py,
              git.py, skills.py, schemas.py
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
sandbox/    — your work area (projects, research, data — put everything here)
```

### OS Process Model

NYX manages requirements as tasks with an OS-like scheduler:

- Each requirement becomes a **task** with its own directory (`task/<tid>/`)
- Tasks have states: `new` → `running` → `done`
- The scheduler picks the next task by priority
- All agent sessions run through `evolver.evolve()` — if repo code changes, auto-commit + restart

### Skills

- **4 base tools** (`bash`, `read`, `write`, `edit`) are the only code-level capabilities.
- **Built-in skills** live in the source repo (`skills/`) and are loaded directly from there.
  They cover generic agent behavior like self-reflection and memory management.
- **Runtime skills** go directly in `skills/<name>/SKILL.md` (under cwd). They override
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

To customize what self-reflect audits, place your own SKILL.md at `skills/self-reflect/SKILL.md` (under cwd) — it shadows the built-in version. NYX can also improve its own SKILL.md during reflection cycles without a restart.

## Safety model

- Evolver controls all code changes — detects repo changes after agent sessions, commits + restarts.
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
        "keep_recent_tokens": 20000,
        "summarize_max_tokens": 1024
    }
}
```

The `compaction` section controls context-window compaction behaviour. All keys are optional — the defaults shown above will be used when the section is omitted.

### Environment Variable Overrides

The following settings can be overridden via environment variables:

| Env Var | Setting | Default |
|---------|---------|----------|
| `NYX_REQ_RETRY_SEC` | seconds between retry attempts for same task | 25 |
| `NYX_SELF_REFLECT_SEC` | seconds between self-reflection cycles | 3600 |

See `app/config.py` for all keys and their defaults.
