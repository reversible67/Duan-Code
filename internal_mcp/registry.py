import re
from typing import Callable

from internal_mcp.client import MCPClientManager
from internal_mcp.policy import allow_mcp_tool_for_lead
from internal_mcp.types import MCPToolSpec


class MCPToolRegistry:
    def __init__(self, client_manager: MCPClientManager):
        self.client_manager = client_manager
        self._tool_specs: dict[str, MCPToolSpec] = {}

    def load_tools(self, tool_specs: list[MCPToolSpec]) -> dict[str, Callable]:
        tool_map: dict[str, Callable] = {}
        self._tool_specs = {}
        for tool_spec in tool_specs:
            if not allow_mcp_tool_for_lead(tool_spec):
                continue
            public_name = self._public_tool_name(tool_spec)
            wrapper = self._build_wrapper(tool_spec, public_name)
            tool_map[public_name] = wrapper
            self._tool_specs[public_name] = tool_spec
        return tool_map

    def get_tool_specs(self) -> dict[str, MCPToolSpec]:
        return dict(self._tool_specs)

    def is_mcp_tool(self, tool_name: str) -> bool:
        return tool_name in self._tool_specs

    def _public_tool_name(self, tool_spec: MCPToolSpec) -> str:
        server_name = self._sanitize_name(tool_spec.server_name)
        tool_name = self._sanitize_name(tool_spec.tool_name)
        return f"mcp_{server_name}_{tool_name}"

    def _sanitize_name(self, value: str) -> str:
        normalized = re.sub(r"\W+", "_", value.strip().lower())
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        return normalized or "tool"

    def _build_wrapper(self, tool_spec: MCPToolSpec, public_name: str) -> Callable:
        def call_mcp_tool(arguments: dict | None = None):
            return self.client_manager.call_tool(tool_spec.server_name, tool_spec.tool_name, arguments or {})

        call_mcp_tool.__name__ = public_name
        call_mcp_tool.__doc__ = (
            f"[MCP:{tool_spec.server_name}] {tool_spec.description}。"
            f"调用时传入一个 dict 参数，对应 server 提供的 input schema。"
        )
        return call_mcp_tool
