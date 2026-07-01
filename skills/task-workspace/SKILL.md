---
name: task-workspace
description: Create and manage per-task workspace directories under sandbox/. Use when starting a new long-running task that needs its own directory, scripts, cron jobs, or state files. Ensures each task's artifacts stay organized in one place.
---

# Task Workspace Skill

This skill guides NYX through setting up a proper workspace for any long-running task or recurring workflow. Every task should own its directory under `sandbox/<task-name>/` and keep all related files there — scripts, state, logs, cron triggers. Nothing leaks into random places.

## Core Principle: One Task, One Directory

Every persistent task gets its own directory:
```
sandbox/<task-name>/
├── README.md          ← What this task is, why it exists
├── progress.md        ← Progress tracking, state, next steps
├── scripts/           ← Cron triggers, automation scripts
│   ├── trigger.sh     ← Inbox task generator (cron calls this)
│   └── helpers/       ← Task-specific helper scripts
├── data/              ← State files, caches, checkpoints
├── insights/          ← Research findings, analysis reports
└── archive/           ← Old sessions, completed work
```

## Before You Start

1. **Check if a workspace already exists** for this task:
   ```bash
   ls sandbox/ | grep -i "<keywords>"
   ```
2. If it exists, use it. If not, create one following the procedure below.

## Procedure

### Step 1: Create the Directory Structure

```bash
TASK_NAME="<task-name>"
mkdir -p "sandbox/$TASK_NAME"/{scripts,data,insights,archive}
```

Use a clear, lowercase-hyphenated name (e.g., `pi-study`, `github-triage`, `weekly-report`).

### Step 2: Write README.md

Create `sandbox/<task-name>/README.md` explaining:
- What this task is and why it exists
- Key files and their purpose
- Cron schedule (if any)
- How to run manually

Example:
```markdown
# <Task Name>

**Purpose:** One-line description of what this task does.
**Cron:** `0 8 * * *` → scripts/trigger.sh (daily at 8 AM)
**Skill:** skills/<skill-name>/SKILL.md (if a dedicated skill exists)

## Files
- progress.md — Current state and next steps
- scripts/trigger.sh — Drops inbox task for the scheduler
- data/state.json — Persistent state between runs

## How to Run
```bash
./sandbox/<task-name>/scripts/trigger.sh
```
```

### Step 3: Move Scripts Here (if they exist elsewhere)

If scripts for this task are scattered in `sandbox/toolbox/scripts/` or other locations, move them:

```bash
# Example: move pi-study scripts from toolbox to its own directory
mv sandbox/toolbox/scripts/pi-*.sh sandbox/pi-study/scripts/ 2>/dev/null
# Update cron to point to new location
```

### Step 4: Register Cron (if needed)

If the task needs periodic execution via cron:

1. Place the trigger script at `sandbox/<task-name>/scripts/trigger.sh`
2. Make it executable: `chmod +x sandbox/<task-name>/scripts/trigger.sh`
3. Add to crontab with the correct path:
   ```bash
   # Add new cron entry
   (crontab -l 2>/dev/null | grep -v "<task-name>"; echo "0 <schedule> /home/llamacpp/nyx/workspace/sandbox/<task-name>/scripts/trigger.sh") | crontab -
   ```

**Cron rules:**
- Each task's cron must point to `sandbox/<task-name>/scripts/`, never to `toolbox/`
- Cron scripts should be idempotent — safe to run multiple times
- Always include error handling (set -euo pipefail, exit 0 on failures)

### Step 5: Create or Update Progress Tracking

Create `sandbox/<task-name>/progress.md` with initial state:
```markdown
# <Task Name> Progress

## Current Status
- Created: $(date '+%Y-%m-%d')
- Last run: N/A
- Next action: <describe next step>

## History
<Append entries here after each run>
```

### Step 6: Clean Up Old Locations

After moving scripts and state to the new workspace:
1. Remove copies from `sandbox/toolbox/` if they were task-specific (not generic)
2. Remove stale directories (e.g., `sandbox/scripts/` if empty)
3. Verify cron entries point to correct locations:
   ```bash
   crontab -l
   # Verify each path exists:
   crontab -l | grep -oP '/sandbox/\S+' | while read f; do [ ! -f "$f" ] && echo "BROKEN: $f"; done
   ```

## Rules

- **No scripts in random locations** — every script belongs in its task's `scripts/` directory or in `toolbox/` if truly generic
- **Cron always points to task-owned scripts**, never to toolbox copies
- **One cron per task** — consolidate multiple triggers into one script if needed
- **README.md must exist** in every task workspace — it's the single source of truth
- **Toolbox is for generic tools only** — reusable utilities that serve multiple tasks (e.g., github_api.py, general patch helpers). Task-specific scripts belong in their own directory.

## When to Use This Skill

- Starting a new recurring task (daily report, monitoring, study session)
- Discovering scripts scattered across sandbox with no clear ownership
- Self-reflect finds cron entries pointing to missing files
- A task needs its own state management beyond what memory/ provides
