import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from memory import MemoryStore


def test_save_memory_creates_file_and_index():
    temp_dir = tempfile.mkdtemp(prefix="memory-store-")
    try:
        store = MemoryStore(os.path.join(temp_dir, ".memory"))
        result = store.save_memory(
            "prefer_tabs",
            "User prefers tabs for indentation",
            "user",
            "The user explicitly prefers tabs over spaces when editing source files.",
        )
        assert "memory 已保存" in result
        assert os.path.isfile(os.path.join(temp_dir, ".memory", "prefer_tabs.md"))
        assert os.path.isfile(os.path.join(temp_dir, ".memory", "MEMORY.md"))
        print("✅ memory 文件与索引创建通过")
    finally:
        shutil.rmtree(temp_dir)


def test_build_memory_section_contains_saved_memory():
    temp_dir = tempfile.mkdtemp(prefix="memory-section-")
    try:
        store = MemoryStore(os.path.join(temp_dir, ".memory"))
        store.save_memory(
            "incident_board",
            "Issue board lives in Linear",
            "reference",
            "Project incidents are usually tracked in the Linear incident board.",
        )
        section = store.build_memory_section()
        assert "incident_board" in section
        assert "reference" in section
        assert "Linear incident board" in section
        print("✅ memory section 构建通过")
    finally:
        shutil.rmtree(temp_dir)


def test_invalid_memory_type_rejected():
    temp_dir = tempfile.mkdtemp(prefix="memory-type-")
    try:
        store = MemoryStore(os.path.join(temp_dir, ".memory"))
        try:
            store.save_memory("bad", "bad", "task", "should fail")
            raise AssertionError("非法 memory type 应抛错")
        except ValueError as e:
            assert "memory type" in str(e)
        print("✅ memory type 校验通过")
    finally:
        shutil.rmtree(temp_dir)


def test_describe_available_returns_index_like_list():
    temp_dir = tempfile.mkdtemp(prefix="memory-list-")
    try:
        store = MemoryStore(os.path.join(temp_dir, ".memory"))
        store.save_memory(
            "approved_pattern",
            "This retry pattern was explicitly approved",
            "feedback",
            "The user accepted this retry strategy as the right pattern for flaky network calls.",
        )
        description = store.describe_available()
        assert "approved_pattern" in description
        assert "feedback" in description
        print("✅ memory 描述列表通过")
    finally:
        shutil.rmtree(temp_dir)


if __name__ == "__main__":
    test_save_memory_creates_file_and_index()
    test_build_memory_section_contains_saved_memory()
    test_invalid_memory_type_rejected()
    test_describe_available_returns_index_like_list()
    print("\n🎉 test_memory 全部通过")
