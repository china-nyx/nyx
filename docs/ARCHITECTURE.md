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
2. Runs LLM session with structured output (merged JSON schema mode)
3. **Modifies repo source directly** via read/write/edit tools
4. If repo changed → evolver detects → auto-commit + restart

**Response format:**
- `thought`: Internal reasoning process
- `tools`: Tool calls needed (bash, read, write, edit)
- `result`: Final answer when no more tools needed

### Hotfixer (`app/hotfixer.py`)

**Purpose:** Mini code-fix agent, 4 base tools only.

**Behavior:**
1. Reads repo source code
2. Implements fixes using read/write/edit tools
3. **Commits changes directly**
4. Returns summary of changes

### Evolver (`app/evolver.py`)

**Purpose:** Run agent session, then commit + restart if repo changed.

**Workflow:**
1. Records HEAD before session
2. Runs agent function (`solver.solve` or `hotfixer.fix`)
3. After session, checks if repo changed
4. If changed → commit + restart via `os.execv`

### Agent (`app/main.py`)

**Main entry point:**

```
inbox/*.md → scheduler creates task/ → agent picks → evolver.evolve(solver) → auto-commit+restart
```

**Tick loop:**
1. Periodic self-reflection (drop inbox file)
2. Ingest inbox files → create tasks
3. Pick next task
4. Execute via `evolver.evolve(solver.solve)`
5. Mark done if no code change

## JSON Schema (Merged Mode)

When both `tools` and `response_format` are present:

```json
{
  "type": "json_schema",
  "json_schema": {
    "name": "agent_response",
    "strict": true,
    "schema": {
      "type": "object",
      "properties": {
        "thought": {"type": "string"},
        "tools": {
          "type": "array",
          "items": {"type": "object", "properties": {"name": "...", "args": {...}}}
        },
        "result": {"type": "object"}
      },
      "required": ["thought", "tools"]
    }
  }
}
```

**Field naming (internal):**
- `thought` — Reasoning process
- `tools` — Tool calls (uses `name` and `args`)
- `result` — Final answer

**OpenAI compatibility:**
- Output: `arguments` (not `args`)
- `name` — tool name

## Bind Mount Behavior

### Solver
- **Read-only bind mount** of main repo
- Can read source code
- If needs to modify → returns result → evolver restarts

### Hotfixer
- **Writeable bind mount** of main repo
- Can modify source code directly
- Commits changes directly

## Skill Override Pattern

Skills loaded from two sources:
1. `repo/skills/` — Built-in skills (version-controlled)
2. `skills/` (runtime) — Instance-specific skills (overrides built-in by name)

When solver loads skills, it scans both directories. Runtime skills shadow built-in ones.

## Multi-turn Tool Calling

NYX uses merged JSON schema mode to support:
1. **Turn 1:** Model returns `tools` array → executor calls tools
2. **Turn 2:** Tools results sent back → Model returns `tools=[]` + `result={...}`

No `oneOf` support in llama-server — schema allows both but logic handles:
- `tools` non-empty → execute tools
- `tools` empty + `result` present → return final answer
- If neither → raise error (retry)
