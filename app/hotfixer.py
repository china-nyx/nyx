"""Hotfixer — mini code-fix agent. 4 base tools only, modifies repo source."""
from app.config import config
from app.session import run_session
from app.prompts import get_hotfixer_template
from sdk.skills import scan_skills


def fix(llm, executor, requirement: str, tid: str = "") -> str:
    """Run a hotfix LLM session. Returns assistant text (for executor → commit message)."""
    system_prompt = get_hotfixer_template(requirement)
    
    skill_index = scan_skills(config.repo / "skills", config.skills_dir)
    skill_prefix = (skill_index + "\n\n" if skill_index else "")
    user = skill_prefix + f"TASK:\n{requirement}"

    return run_session(llm, executor,
                       role="hotfixer", tid=tid,
                       system_prompt=system_prompt,
                       user_content=user,
                       temperature=0.5)
