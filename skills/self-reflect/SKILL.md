---
name: self-reflect
description: NYX's periodic self-introspection cycle. Audits the entire workspace — source code, documentation, skills, sandbox, tasks, and itself — then drives continuous improvement. Every cycle should leave the workspace in a slightly better state.
---

# Self-Reflect Skill

This skill guides NYX through a comprehensive self-reflection cycle that audits **everything** under NYX's control and drives continuous improvement. Unlike task-solving, this is about *meta-cognition* — understanding what NYX knows, what it's doing, what needs fixing, and how to get better.

## Core Principle: Continuous Improvement

Self-reflect is not just a status check — it is NYX's mechanism for **continuous self-improvement**. Every cycle should leave the workspace in a slightly better state than it found it:
- **Summarize**: Compress verbose memory entries. Prune resolved/obsolete content.
- **Update**: Fix any drift between docs/memory and reality.
- **Improve**: If a skill's steps can be clearer, make them clearer. If a script can be more robust, fix it.
- **Discover**: Identify gaps in capabilities or knowledge that no existing task is addressing.

## Before You Start

1. **Read `sandbox/memory/INDEX.md` first** — it lists all memory files with purpose, size, and reading priority
2. Then read the files it marks as must-read (`identity.md`, `goals.md`, etc.)
3. These files tell you who you are, what you've been thinking about, and what's on your plate.

## Reflection Procedure

### Step 0: Survey Current State

```bash
# What tasks am I working on right now?
cat task/active
cat task/index.md | tail -30

# Is there anything new in my inbox?
ls -la mailbox/inbox/

# What's the current time?
date '+%Y-%m-%d %H:%M:%S'

# Workspace overview — what's in sandbox?
find sandbox/ -maxdepth 2 -type f | head -40

# Disk usage — is anything growing unbounded?
du -sh sandbox/ 2>/dev/null
```

---

### Step 1: Audit Source Code (`src/`)

The source code at `src/` (read-only symlink to the git repo) should be healthy, well-documented, and consistent.

#### 1a: Check for TODO/FIXME/HACK Markers

```bash
# Find any lingering TODOs, FIXMEs, HACKs in source
grep -rn "TODO\|FIXME\|HACK\|XXX\|STUB" src/ --include="*.py" | grep -v ".venv"
```
- Note each finding: is it still relevant? Should it be addressed? Add to `issues.md` if actionable.

#### 1b: Check Code Comments and Docstrings

```bash
# Quick scan for modules missing docstrings
python3 -c "
import ast, sys, os
for root, dirs, files in os.walk('src'):
    dirs[:] = [d for d in dirs if d != '.venv']
    for f in files:
        if f.endswith('.py') and not f.startswith('__'):
            path = os.path.join(root, f)
            with open(path) as fh:
                try:
                    tree = ast.parse(fh.read())
                    has_doc = (tree.body and isinstance(tree.body[0], ast.Expr)
                              and isinstance(tree.body[0].value, (ast.Str, ast.Constant)))
                    if not has_doc:
                        print(f'Missing module docstring: {path}')
                except: pass
" 2>/dev/null
```

#### 1c: Check for Dead Code / Unused Imports

```bash
# Quick check for obviously unused imports (heuristic)
python3 -c "
import ast, os
for root, dirs, files in os.walk('src'):
    dirs[:] = [d for d in dirs if d != '.venv']
    for f in files:
        if f.endswith('.py') and not f.startswith('__'):
            path = os.path.join(root, f)
            with open(path) as fh:
                content = fh.read()
            try:
                tree = ast.parse(content)
                imports = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            imports.add(alias.name.split('.')[0])
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imports.add(node.module.split('.')[0])
                used = set(re.findall(r'\b[a-zA-Z_]\w*\b', content))
                unused = imports - used
                if unused:
                    print(f'{path}: possibly unused: {unused}')
            except: pass
" 2>/dev/null
```

#### 1d: Smoke-Check Module Imports

```bash
# Try importing each top-level package to catch broken dependencies early
python3 -c "
import importlib, os, sys
for root, dirs, files in os.walk('src'):
    dirs[:] = [d for d in dirs if d != '.venv']
    for f in files:
        if f == '__init__.py':
            parts = os.path.relpath(root, 'src').split(os.sep)
            module = '.'.join(parts)
            try:
                importlib.import_module(module)
            except Exception as e:
                print(f'Import failed for {module}: {e}')
" 2>/dev/null
```

#### 1e: Cross-Reference Git History with Open Issues

```bash
# Check recent commits for keywords matching open issues
cd src && git log --oneline -20
# Compare against sandbox/memory/issues.md — mark resolved items
```

---

### Step 2: Audit Documentation (`AGENTS.md`, `README.md`)

Documentation should accurately reflect the current codebase. Drift between docs and reality is a maintenance hazard.

#### 2a: Verify AGENTS.md Matches Codebase Structure

```bash
# Check that file layout in AGENTS.md matches actual directory structure
grep -A50 "file layout\|directory structure\|project structure" AGENTS.md 2>/dev/null
# Compare against actual top-level dirs:
ls -d */ 2>/dev/null
```
- Do the listed directories match what actually exists?
- Are there new directories not documented?
- Are described files still present at their stated paths?

#### 2b: Verify README.md Accuracy

```bash
# Check README for outdated claims
grep -i "version\|install\|requirement\|python" README.md | head -10
# Cross-reference with pyproject.toml
head -20 pyproject.toml
```
- Does the README's feature list match what's actually implemented?
- Are installation instructions still correct?

#### 2c: Check for Duplicated Information

```bash
# Quick check: do AGENTS.md and README.md have overlapping content?
# (Manual review — look for sections that could be consolidated)
```
- If info is duplicated across files, note whether it should be consolidated or if duplication is intentional.

#### 2d: Record Findings

If discrepancies found, record in `sandbox/memory/issues.md` under a "Documentation drift" issue. If previously reported drift is resolved, mark RESOLVED.

---

### Step 3: Audit Skills (`skills/*/SKILL.md`)

Skills are NYX's capabilities — they should be accurate, complete, and up-to-date.

#### 3a: List All Skills and Check Descriptions

```bash
for skill_dir in skills/*/; do
    if [ -f "${skill_dir}SKILL.md" ]; then
        echo "=== $(basename $skill_dir) ==="
        head -5 "${skill_dir}SKILL.md"
        echo ""
    fi
done
```

#### 3b: Verify Each Skill Still Works

For each skill:
- **Read the SKILL.md** and verify:
  - Frontmatter `name` and `description` are accurate
  - Steps reference files/paths that actually exist
  - Commands use correct syntax and available tools
  - No references to deprecated features or removed code
- **Check for capability gaps**: Is there something NYX should be able to do but has no skill for? (e.g., automated testing, performance monitoring, dependency management)

#### 3c: Cross-Reference Skills with Source Code

```bash
# Check if skills reference source files that still exist
grep -rh "src/" skills/*/SKILL.md | grep -oP "src/[a-zA-Z0-9_/.-]+" | sort -u | while read f; do
    if [ ! -e "$f" ]; then
        echo "SKILL REFERENCE TO MISSING FILE: $f"
    fi
done
```

#### 3d: Note Skill Improvements

If a skill's steps can be clearer, more robust, or more efficient, note the improvement. Skills in `src/skills/` can be updated via `needs_upgrade`. Skills deployed to runtime `skills/` mirror what's in source.

---

### Step 4: Audit Sandbox Contents (`sandbox/`)

The sandbox is NYX's workspace — it should be organized and useful.

#### 4a: Memory Files (accuracy, drift, completeness)

- **Read all memory files** (use INDEX.md to know which ones exist): `identity.md`, `journal.md`, `goals.md`, `issues.md`
- **Update INDEX.md** after updating any memory file — keep timestamps and sizes current
- **Check for drift**: Cross-reference claims in memory against actual source code
  ```bash
  cd src && git log --oneline -10
  # Verify specific claims in issues.md against actual source
  ```
- **Prune and summarize**:
  - Is `journal.md` getting too large (>50KB)? Summarize old entries, keep recent ones.
  - Are there resolved items in `issues.md` that should be moved to "Resolved" section?
  - Are goals in `goals.md` still relevant? Update statuses.

#### 4b: Clean Up Stale Artifacts

```bash
# Find old temp files, stale scripts, or artifacts
find sandbox/ -maxdepth 1 -type f -name "*.patch" -o -name "*.tmp" -o -name "*.bak" 2>/dev/null
find sandbox/toolbox/ -type f 2>/dev/null | head -20
```
- Identify files that are no longer needed and should be cleaned up
- Look for useful patterns or findings that should be promoted into skills or memory

#### 4c: Check Sandbox Organization

- Is the sandbox structure logical and navigable?
- Are there directories that serve no purpose?
- Should any analysis reports be archived or summarized?

---

### Step 4d: Audit Cron Entries

Cron entries should point to project-owned scripts under `sandbox/projects/<name>/scripts/`, never to missing files or toolbox copies.

```bash
# List all cron entries
crontab -l 2>/dev/null

# Verify each script path exists
crontab -l 2>/dev/null | grep -oP '/sandbox/\S+' | sort -u | while read f; do
    [ -f "$f" ] || echo "BROKEN CRON: $f"
done

# Check for cron pointing to toolbox (should point to project-owned dirs)
crontab -l 2>/dev/null | grep 'toolbox' && echo "WARNING: cron points to toolbox — should be sandbox/projects/<name>/scripts/"
```
- **Broken cron** (script missing): Create the script via `project` skill, or remove the cron entry
- **Cron pointing to toolbox**: Move the script to its project's workspace and update cron
- **Duplicate scripts** (same file in toolbox AND project dir): Keep only the project-owned copy

---

### Step 4e: Audit Projects (`sandbox/projects/`)

Every long-running task should be a project under `sandbox/projects/`, registered in INDEX.md.

```bash
# Check INDEX.md exists and is readable
cat sandbox/projects/INDEX.md 2>/dev/null || echo "MISSING: sandbox/projects/INDEX.md"

# List all project directories (each should have a README.md)
for d in sandbox/projects/*/; do
    [ -f "${d}README.md" ] || echo "MISSING README: $d"
done

# Check for scripts outside projects (leaked into toolbox or random dirs)
find sandbox/toolbox/scripts/ -name "*.sh" 2>/dev/null | while read f; do
    grep -l 'pi-study\|github-triage' "$f" 2>/dev/null && echo "PROJECT-SCRIPT IN TOOLBOX: $f"
done

# Check for project directories at sandbox root (should be under projects/)
for d in sandbox/*/; do
    name=$(basename "$d")
    case "$name" in memory|src|task|toolbox|projects) continue ;; esac
    echo "POSSIBLE UNFILED PROJECT: $d — should be under sandbox/projects/"
done
```
- **Missing INDEX.md**: Create it via `project` skill
- **Project at sandbox root** (e.g., `sandbox/pi-study/`): Migrate to `sandbox/projects/pi-study/`
- **Project scripts in toolbox**: Move them to the appropriate `sandbox/projects/<name>/scripts/`
- **Missing README.md** in a project: Create one via `project` skill

---

### Step 5: Audit Task System (`task/`)

#### 5a: Active Tasks — Progress Assessment

For each active task in `task/active`:
- Read its state, priority, and requirement.md
- Assess progress: is it making headway or stuck?
- Note any tasks that seem to be looping without progress
- Decide if any need attention (priority bump, intervention, cleanup)

#### 5b: Task Index — Size Check

```bash
wc -l task/index.md
# If >100 lines, consider pruning old/done entries
```
- Is `task/index.md` getting too large? Prune very old completed entries.
- Keep recent history for context, but don't let it grow unbounded.

#### 5c: Stuck or Looping Tasks

- Look for tasks that have been in the same state for many cycles
- Check session logs for repeated failure patterns
- If a task is clearly stuck, decide: retry with different approach, deprioritize, or abandon

---

### Step 6: Meta-Reflection (Audit Self-Reflect Itself)

This step is about improving the reflection process itself.

#### 6a: Evaluate This Skill's Procedure

Ask yourself:
- **Is this skill's procedure still optimal?** What steps add value, which are redundant?
- **Are there new areas to audit that aren't covered yet?**
- **Is the order of steps logical?** Should anything be reorganized?
- **Are the bash commands still valid?** Do they produce useful output?

#### 6b: Update SKILL.md if Needed

If you discover improvements to this skill's procedure, note them. The SKILL.md in `src/skills/self-reflect/SKILL.md` can be updated via `needs_upgrade`.

---

### Step 7: Think About What's Missing (Discovery)

Ask yourself:
- **Am I doing everything I should be?** Is there something important that no task is addressing?
- **Is my memory accurate?** Does it reflect current reality, or has it drifted?
- **Are my skills sufficient?** Do I need new capabilities to handle upcoming work?
- **What patterns am I seeing across multiple audits?** (e.g., recurring issues, systemic gaps)
- **Is the reflection mechanism itself working?** Am I getting value from these cycles?

---

### Step 8: Update Memory Files & Take Action

After all audits, consolidate findings and update records.

#### 8a: Update journal.md

Append a new entry with:
- Timestamp
- Summary of findings per audit area (code, docs, skills, sandbox, tasks, self)
- Decisions made and actions taken
- Next priorities

#### 8b: Update goals.md

- Update goal statuses based on audit findings
- Add new goals if gaps discovered
- Remove/archive completed goals

#### 8c: Update issues.md

- Move resolved issues to "Resolved" section
- Add new issues discovered during audit
- Update priorities based on current context

#### 8d: Prune and Summarize

- If `journal.md` exceeds ~50KB, summarize oldest entries and truncate
- Clean up stale sandbox artifacts identified in Step 4d
- If `task/index.md` is too large, prune old completed entries

#### 8e: Take Action — Create Inbox Tasks for Actionable Issues

When you discover actionable problems, **create inbox tasks** so the scheduler resolves them. Don't just record findings and leave them forgotten.

**Action Flow:**
```
self-reflect finds issue → is it actionable? → YES: create inbox task
                                                    NO:  record in journal.md
```

**How to Create an Inbox Task:**
Use the `write` tool to create a `.md` file in `mailbox/inbox/`. The scheduler ingests it on the next tick.

Format:
```
PRIORITY: <N>

<title and description of what needs to be done>
```

**Priority Guidelines:**
- **90**: Critical bugs, broken imports, security issues
- **80**: Code quality (missing docstrings, dead code), documentation drift
- **70**: Skill improvements, new capability gaps
- **60**: Cleanup, organization, non-urgent enhancements

**Rules:**
- Only create tasks for *actionable* items (informational findings go in journal.md)
- Don't duplicate: if an active task already addresses the issue, skip it
- Be specific in the description — include file paths, line numbers, concrete steps
- After creating a task, log it in journal.md ("Created inbox task: 80-fix-doc-drift.md")
- If the fix requires changing NYX source code, still return `needs_upgrade` instead

## Output Format

When done reflecting, write your findings to `sandbox/memory/journal.md` as a new entry. Return status="done" with a brief summary of what you found and decided. If code or skill changes are needed, return status="needs_upgrade".