---
name: memory
description: Manage NYX's persistent memory files in memory/. Use when you need to read, update, or maintain identity, goals, issues, journal, and INDEX.md. Memory is organized into subdirectories with an INDEX.md entry point.
---

# Memory Skill

This skill guides NYX through maintaining its persistent memory files in `memory/`.

## Directory Structure

```
memory/
├── INDEX.md                ← Entry point — lists all files, purpose, size, reading priority
├── identity.md             ← Who NYX is, capabilities, limitations (must-read)
├── goals/                  ← Goal tracking
│   ├── active.md           ← Currently active goals with progress
│   └── archive.md          ← Completed or abandoned goals
├── issues/                 ← Issue tracking
│   ├── open.md             ← Current open issues and limitations
│   └── resolved.md         ← Resolved issues with resolution notes
├── journal/                ← Self-reflection log (append-only, chronological)
│   ├── current.md          ← Current journal entries (pruned when >50KB)
│   └── archive/            ← Compressed older journal entries
│       └── <date>.md       ← Archived by date range
└── specs/                  ← Reference copies of skill specifications
    └── self-reflect.md     ← Self-reflect skill reference (if needed)
```

## Before You Start

1. **Read `memory/INDEX.md`** — it lists all memory files with purpose, size, and reading priority
2. Follow the reading priority: must-read first, then read-when-relevant

## Memory Files

| File | Purpose | Priority |
|------|---------|----------|
| `identity.md` | Who NYX is, architecture components, core loop | Must-read |
| `goals/active.md` | Active goals and progress tracking | Must-read |
| `issues/open.md` | Known issues, limitations, items to fix | Read-when-relevant |
| `journal/current.md` | Self-reflection log (append-only, chronological) | Read latest only |
| `INDEX.md` | Index of all memory files with timestamps and sizes | Must-read first |

## Procedure

### Step 1: Read INDEX.md

```bash
cat memory/INDEX.md
```

This tells you which files exist, their size, and reading priority.

### Step 2: Read Must-Read Files

Always read these first:
- `memory/identity.md` — confirm self-identity is consistent
- `memory/goals/active.md` — check goal progress, decide next actions

### Step 3: Read Relevant Files

Read these when the situation calls for it:
- `memory/issues/open.md` — when encountering anomalies or assessing known limitations
- `memory/journal/current.md` — review recent entries to avoid duplicating work (large file, read latest entries only)

### Step 4: Update Memory

After any significant action or reflection:

**journal/current.md** — Append a new entry with timestamp, findings, decisions made, and next priorities. Do not overwrite existing entries.

**goals/active.md** — Update goal statuses, add/remove goals as needed. Move completed goals to `goals/archive.md`.

**issues/open.md** — Add new issues discovered. Move resolved issues to `issues/resolved.md`.

**INDEX.md** — After updating any memory file, refresh its timestamp and size in the index table.

### Step 5: Prune if Needed

- If `journal/current.md` exceeds ~50KB, summarize oldest entries into `journal/archive/<date>.md` and truncate current
- Move resolved issues from `issues/open.md` to `issues/resolved.md` to keep open file lean
- Move completed goals from `goals/active.md` to `goals/archive.md`

## Migration (existing flat files → subdirectories)

If memory still uses the old flat structure, migrate on next update:

```bash
# Migrate goals
mv memory/goals.md memory/goals/active.md 2>/dev/null
touch memory/goals/archive.md

# Migrate issues
mv memory/issues.md memory/issues/open.md 2>/dev/null
touch memory/issues/resolved.md

# Migrate journal
mkdir -p memory/journal/archive
mv memory/journal.md memory/journal/current.md 2>/dev/null

# Migrate specs (if any)
mkdir -p memory/specs
```

Update INDEX.md to reflect new paths after migration.

## Rules

- Never delete journal entries — only summarize and compress into archive
- INDEX.md must stay current with actual file timestamps and sizes
- `goals/active.md` should be lean — move completed items to archive promptly
- `issues/open.md` should contain only unresolved items
- Memory files are NYX's persistent state across restarts — treat them carefully
