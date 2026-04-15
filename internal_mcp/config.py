import json
import os
from typing import Any

from internal_mcp.types import MCPServerConfig


DEFAULT_MCP_CONFIG_RELATIVE_PATH = os.path.join(".mcp", "config.json")


class MCPConfigError(ValueError):
    pass


def get_mcp_config_path(project_directory: str) -> str:
    return os.path.join(project_directory, DEFAULT_MCP_CONFIG_RELATIVE_PATH)


def load_mcp_server_configs(project_directory: str) -> list[MCPServerConfig]:
    config_path = get_mcp_config_path(project_directory)
    if not os.path.exists(config_path):
        return []
    config_directory = os.path.dirname(config_path)
    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    servers = raw.get("servers", [])
    if not isinstance(servers, list):
        raise MCPConfigError(".mcp/config.json 中的 servers 必须是数组")
    return [_parse_server(item, project_directory, config_directory) for item in servers]


def _parse_server(item: Any, project_directory: str, config_directory: str) -> MCPServerConfig:
    if not isinstance(item, dict):
        raise MCPConfigError("MCP server 配置项必须是对象")
    name = str(item.get("name", "")).strip()
    transport = str(item.get("transport", "stdio")).strip() or "stdio"
    command = str(item.get("command", "")).strip()
    args = item.get("args", [])
    env = item.get("env", {})
    enabled = bool(item.get("enabled", True))
    timeout_seconds = float(item.get("timeout_seconds", 30.0))
    if not name:
        raise MCPConfigError("MCP server 配置缺少 name")
    if transport != "stdio":
        raise MCPConfigError(f"当前仅支持 stdio transport，收到：{transport}")
    if enabled and not command:
        raise MCPConfigError(f"MCP server '{name}' 缺少 command")
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise MCPConfigError(f"MCP server '{name}' 的 args 必须是字符串数组")
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise MCPConfigError(f"MCP server '{name}' 的 env 必须是字符串字典")
    resolved_args = [_resolve_arg_path(arg, project_directory) for arg in args]
    resolved_env = {key: _resolve_env_value(key, value, config_directory) for key, value in env.items()}
    return MCPServerConfig(
        name=name,
        transport=transport,
        command=command,
        args=resolved_args,
        env=resolved_env,
        enabled=enabled,
        timeout_seconds=timeout_seconds,
    )


def _resolve_arg_path(arg: str, project_directory: str) -> str:
    if os.path.isabs(arg):
        return arg
    if arg.endswith((".py", ".js")):
        return os.path.abspath(os.path.join(project_directory, arg))
    return arg


def _resolve_env_value(key: str, value: str, config_directory: str) -> str:
    if os.path.isabs(value):
        return value
    if not value.startswith(("./", ".\\", "../", "..\\")):
        return value
    normalized_key = key.upper()
    if not normalized_key.endswith(("_PATH", "_ROOT", "_DIR")):
        return value
    return os.path.abspath(os.path.join(config_directory, value))
