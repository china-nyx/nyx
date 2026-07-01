---
name: toolbox
description: Manage NYX's shared tool library under sandbox/toolbox/. Use when you need a reusable utility (API wrapper, helper script, patch) that serves multiple tasks. Also use to audit and clean up the toolbox — removing stale entries and task-specific scripts that should live in their own workspace.
---

# Toolbox Skill

This skill guides NYX through managing its shared tool library at `sandbox/toolbox/`. The toolbox is for **generic, reusable utilities** — not task-specific scripts. If a tool only serves one task, it belongs in that task's own workspace directory.

## Core Principle: Shared vs Owned

| Goes in `toolbox/` | Goes in `sandbox/<task>/` |
|---|---|
| github_api.py (used by triage + other tasks) | pi-study-trigger.sh (only pi-study uses it) |
| General patch helpers | daily-pi-check.sh (only pi-study) |
| Utility scripts multiple workflows call | state.json for a specific task |

## Directory Structure

```
sandbox/toolbox/
├── README.md              ← Index of all tools with descriptions
├── scripts/               ← Generic shell utilities (multi-task use only)
│   ├── <tool>.sh
│   └── .<tool>.sh.desc    ← Purpose and usage for each tool
├── helpers/               ← Python helper programs (CLI tools, API wrappers)
│   ├── <tool>.py
│   └── .<tool>.py.desc    ← Purpose and usage for each tool
└── patches/               ← Applied patches kept as reference
    ├── <name>.patch
    └── .<name>.patch.desc ← What it fixes, status (applied/stale)
```

## Procedure

### Step 1: Audit Current Toolbox State

```bash
# List all tools and check their .desc files exist
for f in sandbox/toolbox/scripts/*.sh sandbox/toolbox/helpers/*.py sandbox/toolbox/patches/*.patch; do
    [ -f "$f" ] || continue
    desc="${f}.desc"
    base=$(basename "$f")
    if [ ! -f "${f%$base}.$base.desc" ]; then
        # Check alternate .desc naming
        dot_desc="$(dirname "$f")/.$(basename "$f").desc"
        [ -f "$dot_desc" ] || echo "MISSING DESC: $f"
    fi
done

# Verify toolbox README matches actual contents
echo "=== Files in toolbox ==="
find sandbox/toolbox/ -type f | sort
echo "=== README lists ==="
grep -oP 'sandbox/toolbox/\S+' sandbox/toolbox/README.md | sort -u
```

### Step 2: Check for Task-Specific Scripts That Don't Belong Here

Any script that references a specific task's directory should be moved:

```bash
# Find scripts that hardcode task-specific paths (not generic)
grep -rl "pi-study\|github-triage\|weekly-report" sandbox/toolbox/scripts/ 2>/dev/null
```

If found, move them to the appropriate task workspace and update cron:
```bash
# Example: move a pi-study script out of toolbox
mv sandbox/toolbox/scripts/pi-something.sh sandbox/pi-study/scripts/
rm -f sandbox/toolbox/scripts/.pi-something.sh.desc  # desc stays with the file
```

### Step 3: Check for Stale Patches and Tools

```bash
# Check if patches are still relevant (search source for the fix)
for p in sandbox/toolbox/patches/*.patch; do
    [ -f "$p" ] || continue
    echo "=== $(basename $p) ==="
    cat "${p%.*}.desc" 2>/dev/null || echo "(no desc)"
done
```

Remove patches whose fixes are already in the codebase and no longer needed as reference.

### Step 4: Add New Tool (if creating one)

When adding a new generic utility:

1. Place it in the correct subdirectory (`scripts/`, `helpers/`, or `patches/`)
2. Create a `.desc` file alongside it:
   ```markdown
   # <tool-name>
   **Purpose:** What it does and why it exists
   **Usage:** How to call it (command examples)
   **Config:** Any configuration needed (env vars, files)
   ```
3. Update `sandbox/toolbox/README.md` with the new tool's location and usage
4. Verify no task-specific scripts snuck in

### Step 5: Verify Cron Consistency

```bash
# Check that no cron entry points to toolbox for a task-specific script
crontab -l | grep toolbox
# If any found, verify they are truly generic tools, not task triggers
```

**Rule:** Cron entries should point to `sandbox/<task>/scripts/`, not `sandbox/toolbox/scripts/`. Toolbox scripts are called *by* other scripts, not directly by cron.

### Step 6: Update README.md

After any changes, ensure `sandbox/toolbox/README.md` accurately reflects:
- Current directory structure
- Each tool's purpose and usage
- Clear instructions for adding new tools

## Rules

- **Toolbox is shared infrastructure** — only put things here that serve multiple tasks
- **Every file gets a `.desc`** — no orphaned tools without documentation
- **README.md must stay current** — it's the entry point for discovering toolbox capabilities
- **Task-specific scripts go to task workspaces** — use the `task-workspace` skill for that
- **Cron never calls toolbox directly** — cron triggers live in task-owned directories

## When to Use This Skill

- Adding a new reusable utility (API wrapper, helper, automation)
- Self-reflect finds toolbox drift (stale tools, missing .desc files, README out of date)
- Discovering task-specific scripts that leaked into toolbox
- Cleaning up stale patches or unused helpers
- Auditing cron entries that reference toolbox paths
