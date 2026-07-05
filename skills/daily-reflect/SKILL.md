---
name: daily-reflect
description: NYX's periodic self-introspection cycle. Audits the entire workspace and drives continuous improvement. Every cycle should leave things in a slightly better state.
related_skills: project-management, toolbox
---

# Daily Reflection Skill

This is NYX's mechanism for **continuous self-improvement**. Unlike task-solving, this is meta-cognition — understanding what NYX knows, what it's doing, what needs fixing, and how to get better.

## Before You Start

Read `memory/INDEX.md` to understand what memory files exist, then read the ones that seem relevant. This gives context on current priorities and state.

## Audit Checklist

Go through each area below. For each: **look at the actual state**, compare it to what should be true, and fix or record any problems found.

### 1. Source Code (repo)

- Scan for TODO/FIXME/HACK markers — are they still relevant?
- Check that module docstrings and comments are present and accurate
- Look for dead code or unused imports
- Verify recent commits make sense in context of open issues

### 2. Documentation (README.md, AGENTS.md)

- Does the documentation match the actual codebase structure?
- Are there new files or directories not documented?
- Is any information duplicated across files where it shouldn't be?

### 3. Skills (skills/)

- Read each SKILL.md and verify its steps are still accurate
- Do paths and references in skills point to files that actually exist?
- Are there capability gaps — things NYX should be able to do but has no skill for?
- Improve any skill whose instructions can be clearer or more robust

### 4. Memory (memory/)

- Read all memory files and check for drift between what they say and reality
- Is INDEX.md accurate with current file list, descriptions, and sizes?
- Prune old content: compress verbose entries, archive resolved items, remove files no longer useful
- Remove files that are no longer useful

### 5. Tasks (task/)

- Review active tasks: are any stuck or looping without progress?
- **Check for duplicate tasks**: same requirement type running simultaneously (e.g. two daily-reflection tasks). Remove duplicates and update `active` + `index.md`.
- Is task/index.md getting too large? Prune very old completed entries if needed.
- Check if any tasks need priority adjustment

### 6. Projects and Toolbox (projects/, toolbox/)

- Is projects/INDEX.md accurate with current project list and status?
- Are there stale temp files, artifacts, or empty directories to clean up?
- Should any work products be archived or summarized?
- Check toolbox for stale tools or undocumented entries
- Verify cron entries point to valid scripts

### 7. Daily Reflection Itself

- Is this skill's procedure still optimal? What steps add value, which are redundant?
- Are there new areas to audit that aren't covered?
- If you find improvements, apply them directly to this SKILL.md

## After the Audit

### Update Memory

Record findings in memory: what was discovered, decisions made, actions taken, and next priorities.

### Take Action

For actionable problems discovered during the audit: **create inbox tasks** in `mailbox/inbox/` so the scheduler resolves them. Don't just record findings and forget them.

- Critical bugs or broken code → high priority (90+)
- Code quality or documentation drift → medium priority (70-80)
- Skill improvements or capability gaps → normal priority (60-70)
- Informational observations only → record in memory, no task needed

### Return a Summary

Return a brief summary of what you found and decided.
