from dataclasses import dataclass
from typing import Any, Callable


EXIT_CONTINUE = 0
EXIT_BLOCK = 1
EXIT_APPEND = 2


@dataclass
class HookEvent:
    name: str
    payload: dict[str, Any]


@dataclass
class HookResult:
    exit_code: int = EXIT_CONTINUE
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"exit_code": self.exit_code, "message": self.message}


class HookRunner:
    def __init__(self, hooks: dict[str, list[Callable[[HookEvent], dict[str, Any] | HookResult | None]]] | None = None):
        self.hooks = hooks or {}

    def register(self, event_name: str, handler: Callable[[HookEvent], dict[str, Any] | HookResult | None]) -> None:
        self.hooks.setdefault(event_name, []).append(handler)

    def run(self, event_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        event = HookEvent(name=event_name, payload=payload)
        for handler in self.hooks.get(event_name, []):
            result = self._normalize_result(handler(event))
            if result["exit_code"] in (EXIT_BLOCK, EXIT_APPEND):
                return result
        return HookResult().to_dict()

    @staticmethod
    def _normalize_result(result: dict[str, Any] | HookResult | None) -> dict[str, Any]:
        if result is None:
            return HookResult().to_dict()
        if isinstance(result, HookResult):
            return result.to_dict()
        return {
            "exit_code": int(result.get("exit_code", EXIT_CONTINUE)),
            "message": str(result.get("message", "")),
        }


def on_session_start(event: HookEvent) -> HookResult:
    user_input = str(event.payload.get("user_input", "")).strip()
    if user_input:
        print(f"\n🪝 SessionStart: {user_input}")
    return HookResult()


def pre_tool_guard(event: HookEvent) -> HookResult:
    if event.payload.get("tool_name") != "run_terminal_command":
        return HookResult()
    args = event.payload.get("input", {}).get("args", [])
    kwargs = event.payload.get("input", {}).get("kwargs", {})
    command = kwargs.get("command", args[0] if args else "")
    if isinstance(command, str) and not command.strip():
        return HookResult(exit_code=EXIT_BLOCK, message="Hook 已阻止空命令执行")
    return HookResult()


def post_tool_log(event: HookEvent) -> HookResult:
    tool_name = event.payload.get("tool_name", "")
    print(f"\n🪝 PostToolUse: {tool_name}")
    return HookResult()


def build_default_hook_runner() -> HookRunner:
    runner = HookRunner()
    runner.register("SessionStart", on_session_start)
    runner.register("PreToolUse", pre_tool_guard)
    runner.register("PostToolUse", post_tool_log)
    return runner
