import asyncio
from concurrent.futures import Future
from contextlib import AsyncExitStack
import threading
from typing import Any

from internal_mcp.types import MCPServerConfig, MCPServerState, MCPToolSpec


class MCPClientManager:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._states: dict[str, MCPServerState] = {}
        self._sdk_error = ""

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_coro(self, coro) -> Any:
        future: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def load_servers(self, configs: list[MCPServerConfig]) -> list[MCPToolSpec]:
        self._sessions = {}
        self._states = {
            config.name: MCPServerState(name=config.name, transport=config.transport, connected=False)
            for config in configs
        }
        try:
            self._import_sdk()
        except Exception as exc:
            self._sdk_error = str(exc)
            for state in self._states.values():
                state.last_error = self._sdk_error
            return []
        tool_specs: list[MCPToolSpec] = []
        for config in configs:
            if not config.enabled:
                self._states[config.name].last_error = "server 已禁用"
                continue
            tool_specs.extend(self._run_coro(self._connect_server_async(config)))
        return tool_specs

    def _import_sdk(self) -> None:
        if hasattr(self, "_client_session_cls"):
            return
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        self._client_session_cls = ClientSession
        self._stdio_server_parameters_cls = StdioServerParameters
        self._stdio_client = stdio_client

    async def _connect_server_async(self, config: MCPServerConfig) -> list[MCPToolSpec]:
        state = self._states[config.name]
        exit_stack = AsyncExitStack()
        try:
            server_params = self._stdio_server_parameters_cls(
                command=config.command,
                args=config.args,
                env=config.env or None,
            )
            stdio_transport = await exit_stack.enter_async_context(self._stdio_client(server_params))
            read_stream, write_stream = stdio_transport
            session = await exit_stack.enter_async_context(self._client_session_cls(read_stream, write_stream))
            await session.initialize()
            response = await session.list_tools()
            tool_specs = [
                MCPToolSpec(
                    server_name=config.name,
                    tool_name=tool.name,
                    description=(tool.description or "").strip() or f"来自 {config.name} 的 MCP tool",
                    input_schema=getattr(tool, "inputSchema", {}) or {},
                )
                for tool in response.tools
            ]
            self._sessions[config.name] = {
                "session": session,
                "exit_stack": exit_stack,
            }
            state.connected = True
            state.tool_count = len(tool_specs)
            state.last_error = ""
            return tool_specs
        except Exception as exc:
            await exit_stack.aclose()
            state.connected = False
            state.tool_count = 0
            state.last_error = str(exc)
            return []

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any] | None = None) -> str:
        if self._sdk_error:
            raise RuntimeError(f"MCP SDK 不可用：{self._sdk_error}")
        if server_name not in self._sessions:
            state = self._states.get(server_name)
            detail = state.last_error if state and state.last_error else "server 未连接"
            raise RuntimeError(f"MCP server '{server_name}' 不可用：{detail}")
        return self._run_coro(self._call_tool_async(server_name, tool_name, arguments or {}))

    async def _call_tool_async(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        session = self._sessions[server_name]["session"]
        result = await session.call_tool(tool_name, arguments)
        return self._format_tool_result(result)

    def _format_tool_result(self, result: Any) -> str:
        content = getattr(result, "content", result)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                text = getattr(item, "text", None)
                if text is not None:
                    parts.append(str(text))
                    continue
                if isinstance(item, dict) and "text" in item:
                    parts.append(str(item["text"]))
                    continue
                parts.append(str(item))
            return "\n".join(part for part in parts if part)
        return str(content)

    def get_server_states(self) -> list[MCPServerState]:
        return list(self._states.values())
