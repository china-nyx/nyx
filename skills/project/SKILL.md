---
name: project
description: Create and manage projects under projects/. Use when starting a new long-running task that needs its own workspace (scripts, cron, state files). Also use to audit existing projects — checking INDEX.md accuracy, stale entries, missing READMEs. Every project gets a directory with scripts/, data/, insights/ and is registered in projects/INDEX.md.
---

# Project Skill

This skill guides NYX through creating, organizing, and maintaining projects under `projects/`. Every long-running task or recurring workflow should be a project here — nothing scattered randomly across the workspace.

## Overall Runtime Layout

```
# Runtime root (home/)
├── memory/                     ← NYX self-memory (system — identity, goals, journal)
│   └── INDEX.md                ← Memory file index
├── task/                       ← Scheduler task state (managed by scheduler)
├── projects/                   ← All long-running projects
│   ├── INDEX.md                ← Index of all active projects
│   └── <project-name>/         ← One directory per project
├── toolbox/                    ← Shared utilities (multi-project tools only)
├── temp/                       ← Global scratch space (auto-cleaned on restart)
└── skills/                     ← Runtime skills
```

### projects/INDEX.md

`projects/INDEX.md` is the entry point for discovering active projects. It should contain:

```markdown
# Projects Index

| Project | Status | Last Run | Cron | Description |
|---------|--------|----------|------|-------------|
| pi-study | active | 2025-07-01 | `0 8 * * *` | Continuous study of pi agent project |

## Archived
| Project | Archived | Reason |
|---------|----------|--------|
```

## Project Directory Structure

```
projects/<project-name>/
├── README.md               ← What this project is, why it exists, how to run
├── progress.md             ← Progress tracking, state, next steps
├── scripts/                ← Cron triggers, automation scripts
│   └── trigger.sh          ← Drops inbox task for the scheduler
├── data/                   ← State files, caches, checkpoints (persistent)
├── insights/               ← Research findings, analysis reports
├── temp/                   ← Scratch space for this project only (cleaned periodically)
└── archive/                ← Old sessions, completed work
```

## Procedure

### Step 1: Read INDEX.md (if it exists)

```bash
cat projects/INDEX.md 2>/dev/null || echo "No projects/INDEX.md — first project"
```

Check if a project with the same name already exists. If yes, use its directory.

### Step 2: Create Project Directory

```bash
PROJECT="<project-name>"
mkdir -p "projects/$PROJECT"/{scripts,data,insights,archive}
```

Use clear, lowercase-hyphenated names (e.g., `pi-study`, `github-triage`, `weekly-report`).

### Step 3: Write README.md

```markdown
# <Project Name>

**Purpose:** One-line description.
**Cron:** `<schedule>` → scripts/trigger.sh (or "none")
**Skill:** skills/<skill-name>/SKILL.md (if a dedicated skill exists)

## Files
- progress.md — Current state and next steps
- scripts/trigger.sh — Drops inbox task for the scheduler
- data/state.json — Persistent state between runs

## How to Run
```bash
./projects/<project-name>/scripts/trigger.sh
```
```

### Step 4: Write Initial Progress Tracking

Create `projects/<project-name>/progress.md`:
```markdown
# <Project Name> Progress

## Current Status
- Created: $(date '+%Y-%m-%d')
- Last run: N/A
- Next action: <describe next step>

## History
<Append entries here after each run>
```

### Step 5: Create Trigger Script (if cron needed)

Create `projects/<project-name>/scripts/trigger.sh`:
```bash
#!/bin/bash
set -euo pipefail
INBOX="$(pwd)/mailbox/inbox"
mkdir -p "$INBOX"

TIMESTAMP=$(date '+%Y%m%d-%H%M')
cat > "$INBOX/10-<project-name>-$TIMESTAMP.md" << 'EOF'
Priority: 10

<task description and procedure>
EOF
echo "Created task: $INBOX/10-<project-name>-$TIMESTAMP.md"
```

Then make executable and register cron:
```bash
chmod +x projects/<project-name>/scripts/trigger.sh
# Update crontab (see Step 6)
```

### Step 6: Register Cron (if needed)

```bash
# Add new cron entry, remove any old broken entries for this project
(crontab -l 2>/dev/null | grep -v "<project-name>"; \
 echo "0 <schedule> $(pwd)/projects/<project-name>/scripts/trigger.sh") | crontab -

# Verify
crontab -l | grep "<project-name>"
```

**Cron rules:**
- Always points to `projects/<name>/scripts/` — never to toolbox or random locations
- Scripts must be idempotent (safe to run multiple times)
- Include error handling (`set -euo pipefail`, exit 0 on failures)

### Step 7: Update projects/INDEX.md

**projects/INDEX.md** — add the project row:
```markdown
| <project-name> | active | $(date '+%Y-%m-%d') | `<cron-schedule>` | <description> |
```

Create `projects/INDEX.md` if it doesn't exist (use the format above).

### Step 8: Clean Up Old Locations

If this project's files were previously scattered elsewhere:
1. Move scripts from `toolbox/` or random dirs into the new project directory
2. Remove old empty directories
3. Fix any cron entries pointing to old locations
4. Verify with:
   ```bash
   crontab -l 2>/dev/null | grep -oP '/projects/\S+' | while read f; do [ ! -f "$f" ] && echo "BROKEN: $f"; done
   ```

## When Archiving a Project

When a project is no longer active:
1. Move its directory to `projects/<name>/archive/` or rename with `-archived` suffix
2. Update `projects/INDEX.md` — move from active table to archived table with date and reason
3. Remove its cron entry: `(crontab -l | grep -v "<project-name>") | crontab -`

## Rules

- **All projects under `projects/`** — no project directories at runtime root level
- **projects/INDEX.md must stay current** — lists all projects with status and description
- **Every project has README.md** — single source of truth for what it does and how to run it
- **Cron always points to `projects/<name>/scripts/`**
- **No scripts leaked into toolbox** — project-specific scripts stay in their project directory
- **Temp files go in temp/** — intermediate data, downloads, test output. Never leave them in data/, insights/, or at runtime root
- **`temp/` is global scratch space** — cleaned on each restart; nothing here survives a reboot
- **`projects/<name>/temp/` is project-local scratch space** — cleaned by daily reflection if files are older than 7 days

## When to Use This Skill

- Starting a new recurring task or research effort
- Daily reflection finds projects with missing INDEX.md entries, broken cron, or scattered files
- Migrating existing directories into the proper project structure
- Archiving completed or abandoned projects
- Creating or updating projects/INDEX.md for the first time
