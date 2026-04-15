from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MCPServerConfig:
    name: str
    transport: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    timeout_seconds: float = 30.0


@dataclass(slots=True)
class MCPToolSpec:
    server_name: str
    tool_name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    teammate_allowed: bool = False


@dataclass(slots=True)
class MCPServerState:
    name: str
    transport: str
    connected: bool = False
    tool_count: int = 0
    last_error: str = ""
