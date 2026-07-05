---
name: skill-creator
category: meta
description: Guide for creating new skills from completed complex tasks. Use after completing a task that involved 5+ steps or solved a non-trivial problem.

---

# Skill Creation Workflow

Capture proven patterns as reusable skills after completing a task.

## When to Create a Skill

Create a skill if the completed task meets **any** of these criteria:
- Required 5+ tool calls
- Solved an error or bug that might recur
- Discovered a non-obvious workflow or pattern
- Involved multi-step coordination between tools
- Used knowledge likely to be needed again

## When NOT to Create a Skill

Skip if the task was simple (1-3 steps), too specific to this instance, a similar skill already exists, or the content is a fact rather than a procedure.

## Steps

### 1. Check for Duplicates

Search existing runtime skills and built-in skills (source repo path is in the system prompt under `## Paths`) for overlapping descriptions. If a similar skill exists, update it instead of creating a new one.

### 2. Define the Scope

Answer:
- What specific problem does this skill solve?
- When should NYX use it?
- Is this a procedure (→ skill) or a fact (→ memory)?

### 3. Write SKILL.md

Create `skills/<category>/<skill-name>/SKILL.md` with YAML frontmatter containing `name` and `description`. The body should describe the workflow in steps — what to do and why — not provide copy-paste commands.

### 4. Add Supporting Files (Optional)

Scripts go in `scripts/`, templates in `templates/`, references in `references/` under the skill directory.

## Categories

| Category | Purpose |
|----------|---------|
| `meta` | NYX's own operation |
| `ops` | System operations, maintenance |
| `dev` | Development workflows |
| `research` | Analysis workflows |
| `integration` | External service integrations |

## Quality Checklist

- Name is unique and descriptive
- Description clearly states what the skill does
- Instructions are actionable but not command-specific
- No sensitive data embedded
- Size is reasonable (<5KB for SKILL.md)
