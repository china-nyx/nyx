"""Skill discovery and scanning. Follows Agent Skills standard."""
import os
from pathlib import Path
from typing import List, Tuple

from core import config


def _parse_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter between --- delimiters using simple line-based parsing."""
    result = {}
    lines = text.splitlines()
    in_frontmatter = False

    for line in lines:
        if not in_frontmatter:
            stripped = line.strip()
            if stripped == "---":
                in_frontmatter = True
                continue
            else:
                break  # no frontmatter at all
        else:
            stripped = line.strip()
            if stripped == "---":
                break
            if ":" in stripped:
                key, _, value = stripped.partition(":")
                key = key.strip().lower()
                value = value.strip()
                if len(value) >= 2 and ((value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'")):
                    value = value[1:-1]
                result[key] = value

    return result


def _find_skill_dirs(base_dir: Path) -> List[Path]:
    """Recursively find directories containing SKILL.md files."""
    results = []
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in sorted(dirs) if not d.startswith('.') and d != '__pycache__']
        if 'SKILL.md' in files:
            results.append(Path(root))
    return sorted(results)


def scan_skills() -> str:
    """Scan skills directories for SKILL.md files. Returns XML block or empty string.

    Scans two directories recursively:
      1. CODE/skills/ (built-in, from source repo)
      2. config.SKILLS_DIR (cwd/skills/, runtime — overrides built-in by name)

    Deduplicates by skill name, with runtime dir taking priority."""
    skill_blocks = []
    seen_names = set()

    builtin_dir = config.CODE / "skills"
    runtime_dir = config.SKILLS_DIR

    # Scan built-in first, then runtime (runtime overwrites same-name entries)
    for skills_dir in [builtin_dir, runtime_dir]:
        if not skills_dir.exists():
            continue
        for skill_dir in _find_skill_dirs(skills_dir):
            skill_name = skill_dir.name
            skill_file = skill_dir / "SKILL.md"

            text = skill_file.read_text(encoding="utf-8", errors="ignore")
            fm = _parse_frontmatter(text)
            desc = fm.get("description", "")
            license_val = fm.get("license", "")
            compatibility_val = fm.get("compatibility", "")
            allowed_tools_val = fm.get("allowed-tools", "")
            disable_model = fm.get("disable-model-invocation", "").lower() == "true"

            # Per spec: skills without a description are not loaded
            if not desc:
                continue

            # Name collision: runtime wins (processed last)
            if skill_name in seen_names:
                continue
            seen_names.add(skill_name)

            attrs = f'name="{skill_name}"'
            if license_val:
                attrs += f' license="{license_val}"'
            if compatibility_val:
                attrs += f' compatibility="{compatibility_val}"'
            if allowed_tools_val:
                attrs += f' allowed-tools="{allowed_tools_val}"'

            block = f'<skill {attrs}>\n{desc}\nRead: {skill_file}\n</skill>'
            if not disable_model:
                skill_blocks.append(block)

    return ("<skills>\n" + "\n".join(skill_blocks) + "\n</skills>") if skill_blocks else ""

