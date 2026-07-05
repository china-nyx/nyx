---
name: memory
description: Manage NYX's persistent memory files in memory/. Maintain INDEX.md as the entry point. Other files are created and organized by the agent as needed.

---

# Memory Skill

This skill guides NYX through maintaining its persistent memory files in `memory/`.

## Core Principle

Memory is NYX's persistent state across restarts. The **only required file** is `INDEX.md` — it must always exist and list every other file with a one-line description. Everything else is created by the agent organically as needed (goals, notes, research summaries, etc.).

## INDEX.md

`memory/INDEX.md` is the entry point. It should contain:

```markdown
# Memory Index

| File | Purpose | Last Updated |
|------|---------|-------------|
| <file> | <one-line description> | YYYY-MM-DD |
| ... | ... | ... |
```

**Rules:**
- Always keep INDEX.md accurate — add new files, remove deleted ones
- Include a one-line purpose description for each file
- Keep it concise and scannable

## Reading Memory

When starting work or doing daily reflection:

1. **Read `memory/INDEX.md` first** — understand what exists
2. Read files that are relevant to the current task
3. Do not read everything unless needed

## Writing Memory

Create files as needed. After creating or updating any file, **refresh INDEX.md** to reflect the change.

## Cleanup

Periodically (during daily reflection):
- Remove files that are no longer useful
- Merge or compress large files if they grow unwieldy
- Keep INDEX.md current after any changes
