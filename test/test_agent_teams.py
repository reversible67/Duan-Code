import os
import sys
from unittest.mock import MagicMock
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

tools_mock = types.ModuleType("tools")
for name in ["read_file", "write_to_file", "run_terminal_command", "list_directory", "search_in_files", "web_search", "query_knowledge_base"]:
    setattr(tools_mock, name, MagicMock())
sys.modules["tools"] = tools_mock

from agent import ReActAgent


def test_build_teammate_tools_binds_sender_and_protocol_tools():
    calls = []

    class StubTeamManager:
        def send_message(self, sender, to, content, msg_type="message"):
            calls.append(("send_message", sender, to, content, msg_type))
            return "ok"

        def respond_shutdown(self, sender, request_id, approve, reason=""):
            calls.append(("respond_shutdown", sender, request_id, approve, reason))
            return "shutdown-ok"

        def submit_plan(self, sender, plan):
            calls.append(("submit_plan", sender, plan))
            return "plan-ok"

    agent = ReActAgent.__new__(ReActAgent)
    agent.tools = {
        "read_file": lambda path: path,
        "spawn_teammate": lambda name, role, prompt: prompt,
        "review_plan": lambda request_id, approve, feedback="": feedback,
        "send_message": lambda teammate, content, msg_type="message": content,
    }
    agent.team_manager = StubTeamManager()
    agent._is_mcp_tool = lambda tool_name: False
    agent._make_bound_tool = ReActAgent._make_bound_tool.__get__(agent, ReActAgent)
    agent.build_teammate_tools = ReActAgent.build_teammate_tools.__get__(agent, ReActAgent)

    tools = agent.build_teammate_tools("alice")
    assert "spawn_teammate" not in tools
    assert "send_message" in tools
    assert "respond_shutdown" in tools
    assert "submit_plan" in tools

    tools["send_message"]("lead", "hello")
    tools["respond_shutdown"]("req-1", True, "done")
    tools["submit_plan"]("do work in two steps")

    assert calls[0] == ("send_message", "alice", "lead", "hello", "message")
    assert calls[1] == ("respond_shutdown", "alice", "req-1", True, "done")
    assert calls[2] == ("submit_plan", "alice", "do work in two steps")
    print("✅ teammate tool 绑定通过")


def test_special_commands_bridge_to_team_methods():
    agent = ReActAgent.__new__(ReActAgent)
    agent.get_status = lambda: "status summary"
    agent.list_teammates = lambda: "team roster"
    agent.read_team_inbox = lambda name="lead": f"inbox:{name}"
    agent._handle_special_command = ReActAgent._handle_special_command.__get__(agent, ReActAgent)

    assert agent._handle_special_command("/status") == "status summary"
    assert agent._handle_special_command("/team") == "team roster"
    assert agent._handle_special_command("/inbox") == "inbox:lead"
    assert agent._handle_special_command("/inbox alice") == "inbox:alice"
    assert agent._handle_special_command("not a command") is None
    print("✅ /status、/team 与 /inbox 命令桥接通过")


if __name__ == "__main__":
    test_build_teammate_tools_binds_sender_and_protocol_tools()
    test_special_commands_bridge_to_team_methods()
    print("\n🎉 test_agent_teams 全部通过")
