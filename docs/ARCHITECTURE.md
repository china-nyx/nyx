# NYX Architecture

## Overview

NYX follows an OS process model where each requirement becomes a persistent task.
Tasks cycle through states: `new` → `running` → `done`.

## Core Components

### Scheduler (`app/scheduler.py`)

Manages task lifecycle with persistent state:

```
task/
├── active           ← active (non-done) tids, one per line
├── current_tid      ← tid of task currently being executed
├── index.md         ← human-readable history (all tasks)
└── <tid>/
    ├── state        ← new | running | done
    ├── priority     ← integer (50=default, 10=sched)
    ├── requirement.md
    └── result.md    ← final output when done
```

**Key functions:**
- `create_task()` — Creates new task from inbox file
- `scan_tasks()` — Scans active (non-done) tasks only
- `pick_next_task()` — Selects highest-priority running/new task
- `mark_done()` — Removes task from active set

### Solver (`app/solver.py`)

**Purpose:** Solve tasks using tools and skills.

**Behavior:**
1. Reads available skills from `skills/` directory
2. Runs LLM session with tools support
3. **Modifies repo source directly** via read/write/edit tools
4. Changes are committed by solver itself
5. Executor detects HEAD change → restart

**Note:** Solver has full permissions (bash, read, write, edit) and is expected
to commit changes directly.

### Hotfixer (`app/hotfixer.py`)

**Purpose:** Mini code-fix agent, 4 base tools only.

**Behavior:**
1. Reads repo source code
2. Implements fixes using read/write/edit tools
3. **Commits changes directly**
4. Returns summary of changes

### Executor (`app/executor.py`)

**Purpose:** Run agent session, then restart if repo HEAD changed.

**Workflow:**
1. Records HEAD before session
2. Runs agent function (`solver.solve` or `hotfixer.fix`)
3. After session, checks if HEAD changed
4. If changed → restart via `os.execv`

**Note:** Executor does NOT commit changes. Agents must commit themselves.

### Agent (`app/main.py`)

**Main entry point:**

```
inbox/*.md → scheduler creates task/ → agent picks → executor.run(solver) → restart if changed
```

**Tick loop:**
1. Periodic self-reflection (drop inbox file)
2. Ingest inbox files → create tasks
3. Pick next task
4. Execute via `executor.run(solver.solve)`
5. Mark done after execution

## Tool Calling

Solver uses LLM's tool-calling feature:
- Model returns `tools` array (function calls)
- Executor calls tools and returns results
- Model processes results and returns final answer

No merged JSON schema needed for tool calling.

## Skill Override Pattern

Skills loaded from two sources:
1. `repo/skills/` — Built-in skills (version-controlled)
2. `skills/` (runtime) — Instance-specific skills (overrides built-in by name)

When solver loads skills, it scans both directories. Runtime skills shadow built-in ones.

## Self-Modification

Solver has full permissions to modify NYX's own source code:
- Use write/edit tools on files in `repo/`
- Commit with `git add -A && git commit -m '<brief desc>'`
- Executor detects HEAD change → restart

The solver prompt explicitly encourages self-modification:
"You CAN modify NYX's own source code in {repo}/ to solve tasks."
