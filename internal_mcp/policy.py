from internal_mcp.types import MCPToolSpec


def allow_mcp_tool_for_lead(tool_spec: MCPToolSpec) -> bool:
    return tool_spec.enabled


def allow_mcp_tool_for_teammate(role: str, tool_spec: MCPToolSpec) -> bool:
    return False
