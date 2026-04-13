import os
import re


SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")


def _parse_skill_md(skill_dir: str) -> dict | None:
    """解析技能子目录中的 SKILL.md，返回技能定义字典"""
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        return None

    with open(skill_md, "r", encoding="utf-8") as f:
        content = f.read()

    skill = {"name": os.path.basename(skill_dir), "keywords": [], "steps": []}

    # 解析 ## Description
    desc_match = re.search(r"##\s+Description\s*\n+(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if desc_match:
        skill["description"] = desc_match.group(1).strip()

    # 解析 ## Keywords（每行 "- keyword"）
    kw_match = re.search(r"##\s+Keywords\s*\n+(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if kw_match:
        for line in kw_match.group(1).splitlines():
            kw = line.strip().lstrip("- ").strip()
            if kw:
                skill["keywords"].append(kw)

    # 解析 ## Steps（每行 "N. step"）
    steps_match = re.search(r"##\s+Steps\s*\n+(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if steps_match:
        for line in steps_match.group(1).splitlines():
            step = re.sub(r"^\d+\.\s*", "", line.strip()).strip()
            if step:
                skill["steps"].append(step)

    return skill if skill["steps"] else None


def load_skills() -> list[dict]:
    """遍历 skills/ 目录，加载每个子目录中的 SKILL.md，返回技能列表"""
    skills = []
    if not os.path.isdir(SKILLS_DIR):
        return skills
    for entry in os.listdir(SKILLS_DIR):
        skill_dir = os.path.join(SKILLS_DIR, entry)
        if os.path.isdir(skill_dir):
            skill = _parse_skill_md(skill_dir)
            if skill:
                skills.append(skill)
    return skills


def match_skill(user_input: str, skills: list[dict]) -> dict | None:
    """在任务输入中查找匹配的技能，返回第一个匹配的技能，无匹配返回 None"""
    text = user_input.lower()
    for skill in skills:
        for keyword in skill.get("keywords", []):
            if keyword.lower() in text:
                return skill
    return None
