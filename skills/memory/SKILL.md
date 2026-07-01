---
name: memory
description: Manage NYX's persistent memory files. Use when you need to read, update, or maintain identity.md, goals.md, issues.md, journal.md, and INDEX.md in sandbox/memory/.
---

# Memory Skill

This skill guides NYX through maintaining its persistent memory files in `sandbox/memory/`.

## Before You Start

1. **Read `sandbox/memory/INDEX.md`** — it lists all memory files with purpose, size, and reading priority
2. Follow the reading priority: must-read first, then read-when-relevant

## Memory Files

| File | Purpose |
|------|---------|
| `identity.md` | Who NYX is, architecture components, core loop |
| `goals.md` | Long-term goals and active task tracking |
| `issues.md` | Known issues, limitations, items to fix |
| `journal.md` | Self-reflection log (append-only, chronological) |
| `INDEX.md` | Index of all memory files with timestamps and sizes |

## Procedure

### Step 1: Read INDEX.md

```bash
cat sandbox/memory/INDEX.md
```

This tells you which files exist, their size, and reading priority.

### Step 2: Read Must-Read Files

Always read these first:
- `sandbox/memory/identity.md` — confirm self-identity is consistent
- `sandbox/memory/goals.md` — check goal progress, decide next actions

### Step 3: Read Relevant Files

Read these when the situation calls for it:
- `sandbox/memory/issues.md` — when encountering anomalies or assessing known limitations
- `sandbox/memory/journal.md` — review recent entries to avoid duplicating work (large file, read latest entries only)

### Step 4: Update Memory

After any significant action or reflection:

**journal.md** — Append a new entry with timestamp, findings, decisions made, and next priorities. Do not overwrite existing entries.

**goals.md** — Update goal statuses, add/remove goals as needed.

**issues.md** — Move resolved issues to "Resolved" section, add new issues discovered.

**INDEX.md** — After updating any memory file, refresh its timestamp and size in the index table.

### Step 5: Prune if Needed

- If `journal.md` exceeds ~50KB, summarize oldest entries and truncate
- Clean up stale or obsolete content from all files

## Rules

- Never delete journal entries — only summarize and compress
- INDEX.md must stay current with actual file timestamps and sizes
- Memory files are NYX's persistent state across restarts — treat them carefully
