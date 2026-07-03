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
├── solver.py      ← Solve tasks, may modify code
├── hotfixer.py    ← Hotfix, modifies code
├── executor.py    ← Run + restart
├── scheduler.py   ← Task management
└── main.py        ← Main entry
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
