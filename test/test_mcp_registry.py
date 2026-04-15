import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from internal_mcp.registry import MCPToolRegistry
from internal_mcp.types import MCPToolSpec


class StubClientManager:
    def __init__(self):
        self.calls = []

    def call_tool(self, server_name, tool_name, arguments):
        self.calls.append((server_name, tool_name, arguments))
        return "mcp-result"


def test_mcp_registry_wraps_tools_with_prefixed_public_names():
    client_manager = StubClientManager()
    registry = MCPToolRegistry(client_manager)
    tool_specs = [
        MCPToolSpec(
            server_name="filesystem",
            tool_name="read_text",
            description="读取文本资源",
            input_schema={"type": "object"},
        )
    ]

    tools = registry.load_tools(tool_specs)

    assert "mcp_filesystem_read_text" in tools
    assert registry.is_mcp_tool("mcp_filesystem_read_text") is True
    result = tools["mcp_filesystem_read_text"]({"path": "README.md"})
    assert result == "mcp-result"
    assert client_manager.calls == [("filesystem", "read_text", {"path": "README.md"})]


if __name__ == "__main__":
    test_mcp_registry_wraps_tools_with_prefixed_public_names()
    print("✅ test_mcp_registry 通过")
