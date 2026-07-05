---
name: toolbox
description: Manage NYX's shared tool library under toolbox/. For reusable utilities that serve multiple projects. Audit the toolbox to remove stale entries and project-specific scripts.
related_skills: project-management
---

# Toolbox Skill

Manage the shared utility library at `toolbox/`. Only generic, multi-project tools belong here — project-specific scripts go in their own `projects/<project>/` directory.

## Principles

- Organize files however makes sense for the tools you have
- Document each tool's purpose and usage alongside it (e.g., a `.desc` file or comments)
- Keep an index (README.md or similar) so tools can be discovered

## Auditing the Toolbox

When reviewing the toolbox:

1. Check that every tool has documentation describing what it does
2. Find project-specific scripts — they should be moved to their project directory
3. Remove stale tools and patches no longer needed
4. Verify cron consistency — no cron entry should call toolbox scripts directly; cron triggers live in `projects/<project>/scripts/`

## Rules

- Toolbox is shared infrastructure — only multi-project tools
- Project-specific scripts go to their project directory
- Cron never calls toolbox directly
