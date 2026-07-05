---
name: project
description: Create and manage projects under projects/. Each project gets its own directory with scripts, data, and state files. Register every project in projects/INDEX.md.
related_skills: toolbox, daily-reflect
---

# Project Skill

Manage long-running tasks and recurring workflows under `projects/`. Every project gets a directory and an entry in `projects/INDEX.md`.

## Directory Structure

Each project has:
- `README.md` — purpose, how to run, file descriptions
- `progress.md` — current state and next steps
- `scripts/` — trigger scripts (e.g., for cron)
- `data/` — persistent state files
- `insights/` — research findings
- `temp/` — scratch space (cleaned periodically)
- `archive/` — old sessions, completed work

## Creating a Project

1. Read `projects/INDEX.md` to check for duplicates
2. Create the directory with subdirectories (`scripts/`, `data/`, `insights/`, `archive/`)
3. Write `README.md` describing purpose and file layout
4. Write initial `progress.md` with creation date and next actions
5. If cron is needed, create a trigger script in `scripts/` and register it
6. Add the project to `projects/INDEX.md`

Use clear, lowercase-hyphenated names (e.g., `weekly-report`).

## Trigger Scripts

Trigger scripts live in `scripts/` and drop inbox files for the scheduler. They must be idempotent and include error handling.

## Cron Rules

- Cron always points to `projects/<name>/scripts/`, never to toolbox
- Scripts must be idempotent
- Include error handling (`set -euo pipefail`)

## Updating INDEX.md

`projects/INDEX.md` lists all active projects with status, last run date, cron schedule, and description. Keep it current — add new projects, update status after runs, move completed ones to an archived section.

## Archiving

When a project is no longer active:
1. Move its content to `archive/` or rename the directory with `-archived` suffix
2. Update `projects/INDEX.md` — move to archived section with date and reason
3. Remove its cron entry

## Rules

- All projects under `projects/`, nothing at runtime root level
- `projects/INDEX.md` must stay current
- Every project has a `README.md`
- Project-specific scripts stay in their own directory, never in toolbox
- Temp files go in `temp/`, cleaned on restart
