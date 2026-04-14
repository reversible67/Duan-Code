import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from skills import SkillRegistry, load_skills, load_skill, match_skill


def test_load_skills_returns_manifests_only():
    manifests = load_skills()
    assert manifests, "应至少发现一个 skill"
    assert all("name" in skill and "description" in skill for skill in manifests)
    assert all("steps" not in skill for skill in manifests)
    print("✅ manifest 轻量发现通过")


def test_registry_describe_available():
    registry = SkillRegistry()
    description = registry.describe_available()
    assert "analyze-code" in description
    assert "list-files" in description
    print("✅ 技能目录描述通过")


def test_match_skill_by_keyword():
    skill = match_skill("请帮我分析代码质量")
    assert skill is not None
    assert skill["name"] == "analyze-code"
    print("✅ 关键词匹配通过")


def test_load_skill_returns_full_body_and_resource_index():
    content = load_skill("analyze-code")
    assert "<skill name=\"analyze-code\">" in content
    assert "## Steps" in content
    assert "reference.md" in content
    assert "函数数量" not in content
    print("✅ 技能正文按需加载与资源渐进披露通过")


if __name__ == "__main__":
    test_load_skills_returns_manifests_only()
    test_registry_describe_available()
    test_match_skill_by_keyword()
    test_load_skill_returns_full_body_and_resource_index()
    print("\n🎉 test_skills 全部通过")
