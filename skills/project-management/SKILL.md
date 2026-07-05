---
name: project-management
description: Create and manage projects under projects/. Each project gets its own directory with scripts, data, and state files. Register every project in projects/INDEX.md.
related_skills: toolbox
---

# Project Skill

Manage long-running tasks and recurring workflows under `projects/`. Every project gets a directory and an entry in `projects/INDEX.md`.

## Creating a Project

1. Read `projects/INDEX.md` to check for duplicates
2. Create a directory with whatever subdirectories the project needs (e.g., scripts, data, temp)
3. Document the project's purpose and file layout
4. Add the project to `projects/INDEX.md`

Use clear, lowercase-hyphenated names.

## Trigger Scripts

If cron is needed, create trigger scripts that drop inbox files for the scheduler. They must be idempotent and include error handling.

## Cron Rules

- Cron always points to `projects/<name>/scripts/`, never to toolbox
- Scripts must be idempotent
- Include error handling (`set -euo pipefail`)

## Updating INDEX.md

`projects/INDEX.md` lists all active projects with status and description. Keep it current — add new projects, update status after runs, move completed ones to an archived section.

## Archiving

When a project is no longer active:
1. Move its content aside or rename the directory
2. Update `projects/INDEX.md` — move to archived section with date and reason
3. Remove its cron entry

## Rules

- All projects under `projects/`, nothing at runtime root level
- `projects/INDEX.md` must stay current
- Project-specific scripts stay in their own directory, never in toolbox
- Temp files go in `temp/`, cleaned on restart
