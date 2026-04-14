import os
import re


MEMORY_TYPES = ("user", "feedback", "project", "reference")


def _read_text(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", content, re.DOTALL)
    if not match:
        return {}, content

    raw_meta, body = match.groups()
    meta = {}
    for raw_line in raw_meta.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, body


def _safe_name(name: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip().lower())
    return value.strip("_")


class MemoryStore:
    def __init__(self, memory_dir: str):
        self.memory_dir = memory_dir

    def _ensure_dir(self) -> None:
        os.makedirs(self.memory_dir, exist_ok=True)

    def _memory_path(self, name: str) -> str:
        safe_name = _safe_name(name)
        if not safe_name:
            raise ValueError("memory name 不能为空")
        return os.path.join(self.memory_dir, f"{safe_name}.md")

    def _index_path(self) -> str:
        return os.path.join(self.memory_dir, "MEMORY.md")

    def list_memories(self) -> list[dict]:
        if not os.path.isdir(self.memory_dir):
            return []

        memories = []
        for entry in sorted(os.listdir(self.memory_dir)):
            if not entry.endswith(".md") or entry == "MEMORY.md":
                continue
            file_path = os.path.join(self.memory_dir, entry)
            meta, body = _parse_frontmatter(_read_text(file_path))
            memories.append({
                "name": meta.get("name") or os.path.splitext(entry)[0],
                "description": meta.get("description", ""),
                "type": meta.get("type", "reference"),
                "content": body.strip(),
                "path": file_path,
            })
        return memories

    def rebuild_index(self) -> str:
        self._ensure_dir()
        memories = self.list_memories()
        lines = ["# Memory Index", ""]
        if not memories:
            lines.append("- 暂无可用 memory")
        else:
            for memory in memories:
                lines.append(f"- {memory['name']}: {memory['description']} [{memory['type']}]")
        content = "\n".join(lines) + "\n"
        with open(self._index_path(), "w", encoding="utf-8") as f:
            f.write(content)
        return content

    def save_memory(self, name: str, description: str, mem_type: str, content: str) -> str:
        if mem_type not in MEMORY_TYPES:
            allowed = ", ".join(MEMORY_TYPES)
            raise ValueError(f"memory type 必须是以下之一：{allowed}")

        memory_name = name.strip()
        memory_description = description.strip()
        memory_content = content.strip()
        if not memory_name:
            raise ValueError("memory name 不能为空")
        if not memory_description:
            raise ValueError("memory description 不能为空")
        if not memory_content:
            raise ValueError("memory content 不能为空")

        self._ensure_dir()
        payload = (
            "---\n"
            f"name: {memory_name}\n"
            f"description: {memory_description}\n"
            f"type: {mem_type}\n"
            "---\n"
            f"{memory_content}\n"
        )
        path = self._memory_path(memory_name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(payload)
        self.rebuild_index()
        return f"memory 已保存：{path}"

    def describe_available(self) -> str:
        memories = self.list_memories()
        if not memories:
            return "- 暂无可用长期记忆"
        return "\n".join(
            f"- {memory['name']}: {memory['description']} [{memory['type']}]"
            for memory in memories
        )

    def build_memory_section(self) -> str:
        memories = self.list_memories()
        if not memories:
            return "- 暂无可用长期记忆"

        blocks = [
            "以下是可供参考的长期记忆。它们只提供方向，不替代当前观察；如果与当前代码或资源冲突，优先相信你刚刚验证到的真实状态。"
        ]
        for memory in memories:
            blocks.append(
                f"<memory name=\"{memory['name']}\" type=\"{memory['type']}\">\n"
                f"描述：{memory['description']}\n"
                f"内容：{memory['content']}\n"
                f"</memory>"
            )
        return "\n\n".join(blocks)
