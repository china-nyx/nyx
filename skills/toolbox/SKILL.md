---
name: toolbox
description: Manage NYX's shared tool library under sandbox/toolbox/. Use when you need a reusable utility (API wrapper, helper script, patch) that serves multiple projects. Also use to audit the toolbox — removing stale entries and project-specific scripts that should live in their own project directory.
---

# Toolbox Skill

This skill guides NYX through managing its shared tool library at `sandbox/toolbox/`. The toolbox is for **generic, reusable utilities** — not project-specific scripts. If a tool only serves one project, it belongs in `sandbox/projects/<project>/`.

## Core Principle: Shared vs Owned

| Goes in `toolbox/` | Goes in `sandbox/projects/<project>/` |
|---|---|
| github_api.py (used by triage + other projects) | pi-study-trigger.sh (only pi-study uses it) |
| General patch helpers | daily-pi-check.sh (only pi-study) |
| Utility scripts multiple workflows call | state.json for a specific project |

## Directory Structure

```
sandbox/toolbox/
├── README.md              ← Index of all tools with descriptions
├── scripts/               ← Generic shell utilities (multi-project use only)
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
find sandbox/toolbox/ -type f ! -name ".*.desc" | sort
echo "=== README lists ==="
grep -oP 'sandbox/toolbox/\S+' sandbox/toolbox/README.md 2>/dev/null | sort -u

# Check for missing .desc files
for f in $(find sandbox/toolbox/scripts/ sandbox/toolbox/helpers/ sandbox/toolbox/patches/ -type f ! -name ".*.desc" 2>/dev/null); do
    dot_desc="$(dirname "$f")/.$(basename "$f").desc"
    [ -f "$dot_desc" ] || echo "MISSING DESC: $f"
done
```

### Step 2: Check for Project-Specific Scripts That Don't Belong Here

Any script that references a specific project's directory should be moved to `sandbox/projects/<project>/`:

```bash
# Find scripts that hardcode project-specific paths
grep -rl 'pi-study\|github-triage' sandbox/toolbox/scripts/ 2>/dev/null
```

If found, move them:
```bash
mv sandbox/toolbox/scripts/pi-something.sh sandbox/projects/pi-study/scripts/
# Update any cron entries pointing to the old location
```

### Step 3: Check for Stale Patches and Tools

```bash
for p in sandbox/toolbox/patches/*.patch; do
    [ -f "$p" ] || continue
    echo "=== $(basename $p) ==="
    cat "$(dirname "$p")/.$(basename "$p").desc" 2>/dev/null || echo "(no desc)"
done
```

Remove patches whose fixes are already in the codebase and no longer needed.

### Step 4: Add New Tool (if creating one)

1. Place it in the correct subdirectory (`scripts/`, `helpers/`, or `patches/`)
2. Create a `.desc` file alongside it:
   ```markdown
   # <tool-name>
   **Purpose:** What it does and why it exists
   **Usage:** How to call it (command examples)
   **Config:** Any configuration needed (env vars, files)
   ```
3. Update `sandbox/toolbox/README.md` with the new tool's location and usage

### Step 5: Verify Cron Consistency

```bash
# No cron entry should point to toolbox for a project-specific script
crontab -l 2>/dev/null | grep 'toolbox'
```

**Rule:** Cron entries should point to `sandbox/projects/<project>/scripts/`, never to `sandbox/toolbox/scripts/`. Toolbox scripts are called *by* other scripts, not directly by cron.

## Rules

- **Toolbox is shared infrastructure** — only put things here that serve multiple projects
- **Every file gets a `.desc`** — no orphaned tools without documentation
- **README.md must stay current** — it's the entry point for discovering toolbox capabilities
- **Project-specific scripts go to `sandbox/projects/<project>/`** — use the `project` skill for that
- **Cron never calls toolbox directly** — cron triggers live in project-owned directories

## When to Use This Skill

- Adding a new reusable utility (API wrapper, helper, automation)
- Self-reflect finds toolbox drift (stale tools, missing .desc files, README out of date)
- Discovering project-specific scripts that leaked into toolbox
- Cleaning up stale patches or unused helpers
- Auditing cron entries that reference toolbox paths
