import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hooks import HookEvent, HookResult, HookRunner, EXIT_APPEND, EXIT_BLOCK, build_default_hook_runner


def test_hook_runner_continue():
    runner = HookRunner()
    runner.register("SessionStart", lambda event: HookResult())
    result = runner.run("SessionStart", {"user_input": "hello"})
    assert result == {"exit_code": 0, "message": ""}
    print("✅ continue 语义通过")


def test_hook_runner_block():
    runner = HookRunner()
    runner.register("PreToolUse", lambda event: {"exit_code": EXIT_BLOCK, "message": "blocked"})
    result = runner.run("PreToolUse", {"tool_name": "run_terminal_command", "input": {"args": [""]}})
    assert result == {"exit_code": 1, "message": "blocked"}
    print("✅ block 语义通过")


def test_hook_runner_append():
    runner = HookRunner()
    runner.register("PostToolUse", lambda event: HookResult(exit_code=EXIT_APPEND, message="note"))
    result = runner.run("PostToolUse", {"tool_name": "read_file", "output": "ok"})
    assert result == {"exit_code": 2, "message": "note"}
    print("✅ append 语义通过")


def test_default_pre_tool_guard_blocks_empty_command():
    runner = build_default_hook_runner()
    result = runner.run("PreToolUse", {"tool_name": "run_terminal_command", "input": {"args": [""]}})
    assert result["exit_code"] == 1
    assert "空命令" in result["message"]
    print("✅ 默认 PreToolUse guard 生效")


if __name__ == "__main__":
    test_hook_runner_continue()
    test_hook_runner_block()
    test_hook_runner_append()
    test_default_pre_tool_guard_blocks_empty_command()
    print("\n🎉 test_hooks 全部通过")
