---
name: self-improve
description: Guide for improving NYX itself. Use when you find a way to make your own code, prompts, skills, or workflows better. Covers two mechanisms: modifying source code (permanent) and spawning new tasks via inbox (deferred).
---

# Self-Improvement Skill

NYX can improve itself in two ways. Choose the right one for the situation.

## Mechanism 1 — Modify Source Code (Immediate)

Use when you need to change how NYX works: code, prompts, hooks, built-in skills.

1. Make the changes with read/write/edit
2. Commit: `git add -A && git commit -m '[self] <message>'`
   `[self]` marks this as an auto-upgrade (not a manual human change). Write `<message>` following Conventional Commits convention.
3. Return your progress notes — NYX will restart with the upgraded code

## Mechanism 2 — Spawn a New Task (Deferred)

Use when you want to do something that doesn't require changing source code: research, learning from external projects, long-running studies, generating new content.

Write a `.md` file into `mailbox/inbox/`. The scheduler will pick it up and execute it as a normal task.

- Filename convention: `<priority>-<name>.md` (e.g. `50-daily-learn.md`)
- Priority is a number (higher = more urgent, 50 is default)
- Content is the requirement text — just say what to do

If the spawned task itself needs recurring execution, use the project-management skill to set up trigger scripts.

## When to Use Which

- **Code change needed?** → Mechanism 1 (modify source + commit)
- **Research, learning, or multi-step work?** → Mechanism 2 (spawn inbox task)
- **Both?** → Do mechanism 1 first if the code change unblocks the task, then spawn the task
