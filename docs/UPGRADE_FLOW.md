# NYX Upgrade Flow

## Overview

NYX's upgrade flow uses a simplified direct modification approach:
- Solver/Hotfixer directly modifies the main repo code
- After committing, Executor detects HEAD changes → restart
- No complex subtask mechanism

## Why Simplified?

**Problems with previous upgrader subtask design:**
1. Added complexity: needed to manage parent-child tasks, state transitions
2. Most scenarios don't need to distinguish "solve" vs "upgrade"
3. Solver has full permissions to modify code, no need to restrict

**Benefits of simplified design:**
1. Simpler: direct modification + commit
2. More flexible: Solver can do anything
3. More reliable: No state synchronization issues

## Current Flow

```
1. Solver runs
   - Reads skills
   - Executes task (may modify code)
   - Commits changes

2. Executor detects
   - Records HEAD
   - Runs solver
   - Checks if HEAD changed

3. Restart
   - If HEAD changed → os.execv restart
   - If no change → normal exit
```

## File Structure

```
app/
├── boot.py         ← Bootstrap, self-heal on crash
├── config.py       ← Settings from config/settings.json
├── executor.py     ← Run agent session, restart on HEAD change
├── hotfixer.py     ← Mini code-fix agent (4 tools only)
├── log.py          ← Logging setup
├── main.py         ← Agent tick loop (self-reflect, scheduler, executor)
├── prompts.py      ← System prompt templates for solver/hotfixer
├── scheduler.py    ← Task lifecycle management
├── self_heal.py    ← Crash recovery via hotfixer
├── self_reflect.py ← Periodic self-audit task generation
├── session.py      ← Shared session runner (JSONL logging, on_step)
└── solver.py       ← Solve tasks with tools + skills
```

```
sdk/
├── agent.py        ← Tool-calling loop, context compaction
├── compaction.py   ← Token estimation, summarization
├── fs.py           ├── Filesystem helpers (ensure_dir, atomic_write)
├── git.py          ├── Git wrapper (short, dirty, commit)
├── llm.py          ├── OpenAI-compatible HTTP client
├── schemas.py      ├── Pydantic models for LLM requests/responses
├── skills.py       ├── Skill discovery and scanning
└── tools.py        └── 4 base tools: bash, read, write, edit
```

## Agent Communication

**No complex communication:**
- Solver doesn't return special status
- Executor only detects HEAD changes
- No state sync, subtasks, parent restoration

**Direct commit:**
```bash
git add -A && git commit -m 'fix: brief description'
```

Executor detects HEAD change → restart.
