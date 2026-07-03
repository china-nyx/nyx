# NYX Agent Guidelines

This document is for **developers** working on NYX itself. For user-facing documentation (what NYX does, how to run it, configuration), see [README.md](README.md).

## Architecture Principles

- **4 base tools only**: `bash`, `read`, `write`, `edit`. Everything else is a skill.
- **Code changes via executor**: agent sessions run through `executor.run()` which detects repo HEAD changes → commit + `os.execv` restart.
- **Skills over tools**: New capabilities go in `skills/<name>/` as SKILL.md + scripts, NOT as new `_t_*` methods in `sdk/tools.py`.
- **Skill pattern**: Each skill is a directory with `SKILL.md` (frontmatter: name, description) and optional `scripts/` subdirectory. The LLM reads the SKILL.md via `read`, then executes steps using `bash` to call scripts.
- **All code is modifiable**: solver can modify repo source directly — executor detects changes and commits + restarts.
- **Hotfixer is the stable core**: `app/hotfixer.py` only depends on `app/config.py` + `sdk/`. Boot invokes it directly when anything fails.

## Skill Development

NYX follows the [Agent Skills standard](https://agentskills.io/specification.md).

**Generic skills** (useful for any NYX instance) go in `repo/skills/<name>/`. They are loaded directly from the source repo at runtime, no deployment step needed.

1. Create `SKILL.md` with frontmatter (name, description) and usage instructions
2. Add helper scripts in `scripts/` subdirectory if needed
3. Scripts reference paths relative to the runtime root (cwd)
4. Changes are promoted through executor (commit → restart)

**Instance-specific skills** go directly in `skills/<name>/SKILL.md` (under cwd) — no code change needed.
Runtime skills override built-in ones by name: if `skills/<name>/` exists, it shadows
`repo/skills/<name>/`. The runtime skills directory has its own git repo for version control.

## Git Workflow

- After every verified working change: `git add -A && git commit -m '<desc>'`
- Executor auto-commits code changes after agent sessions that modify the repo

## Cross-references

Topics covered in README.md (see there for details):

- [How NYX works](README.md#how-it-works) — solver / hotfixer / executor flow
- [OS Process Model](README.md#os-process-model) — task lifecycle and scheduling
- [Skills overview](README.md#skills) — runtime vs built-in skills
- [Self-Reflection](README.md#self-reflection) — periodic self-audit
- [Running NYX](README.md#running-it) — setup, systemd, launching
- [Sending Tasks](README.md#sending-tasks) — filename convention (priority parsed from prefix)
- [Configuration](README.md#configuration) — settings.json keys and env var overrides
