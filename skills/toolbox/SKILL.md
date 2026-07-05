---
name: toolbox
description: Manage NYX's shared tool library under toolbox/. For reusable utilities (scripts, helpers, patches) that serve multiple projects. Audit the toolbox to remove stale entries and project-specific scripts.
related_skills: project, daily-reflect
---

# Toolbox Skill

Manage the shared utility library at `toolbox/`. Only generic, multi-project tools belong here — project-specific scripts go in their own `projects/<project>/` directory.

## Directory Structure

```
toolbox/
├── README.md              ← Index of all tools with descriptions
├── scripts/               ← Shell utilities (multi-project only)
│   ├── <tool>.sh
│   └── .<tool>.sh.desc    ← Purpose and usage
├── helpers/               ← Python helpers (CLI tools, API wrappers)
│   ├── <tool>.py
│   └── .<tool>.py.desc    ← Purpose and usage
└── patches/               ← Applied patches as reference
    ├── <name>.patch
    └── .<name>.patch.desc ← What it fixes, status
```

Every tool file has a corresponding `.desc` file documenting its purpose and usage.

## Auditing the Toolbox

When reviewing the toolbox:

1. **List all tools** — compare against `README.md` to find missing or orphaned entries
2. **Check .desc files** — every tool needs one; flag any that are missing
3. **Find project-specific scripts** — scripts referencing a particular project's directory should be moved to that project
4. **Review patches** — remove patches whose fixes are already merged into the codebase
5. **Verify cron consistency** — no cron entry should call toolbox scripts directly; cron triggers live in `projects/<project>/scripts/`

## Adding a New Tool

1. Place it in the correct subdirectory (`scripts/`, `helpers/`, or `patches/`)
2. Create a `.desc` file documenting purpose, usage, and configuration
3. Update `toolbox/README.md`

## Rules

- Toolbox is shared infrastructure — only multi-project tools
- Every file gets a `.desc`
- `README.md` must stay current
- Project-specific scripts go to their project directory
- Cron never calls toolbox directly
