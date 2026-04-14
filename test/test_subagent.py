import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 在导入 agent 之前 mock 掉 tools 模块，绕过 ddgs 等外部依赖
from unittest.mock import MagicMock
import types
tools_mock = types.ModuleType("tools")
for name in ["read_file", "write_to_file", "run_terminal_command",
             "list_directory", "search_in_files", "web_search", "query_knowledge_base"]:
    setattr(tools_mock, name, MagicMock())
sys.modules["tools"] = tools_mock

from agent import SubagentContext, ReActAgent

def mock_read_file(path): return f"[内容] {path}: hello"
def mock_list_directory(path): return f"[目录] {path}: a.py"
MOCK_TOOLS = {"read_file": mock_read_file, "list_directory": mock_list_directory}

def _simple_parse_action(code_str):
    """简易版 parse_action，不依赖 self"""
    import re, ast
    match = re.match(r'(\w+)\((.*)\)', code_str, re.DOTALL)
    if not match:
        raise ValueError("Invalid syntax")
    func_name = match.group(1)
    args_str = match.group(2).strip()
    if not args_str:
        return func_name, []
    args = []
    for part in args_str.split(','):
        part = part.strip()
        if (part.startswith('"') and part.endswith('"')) or (part.startswith("'") and part.endswith("'")):
            args.append(part[1:-1])
        else:
            try:
                args.append(ast.literal_eval(part))
            except (SyntaxError, ValueError):
                args.append(part)
    return func_name, args

def make_mock_agent(responses):
    i = [0]
    class FakeAgent:
        def dispatch_model(self, messages):
            idx = min(i[0], len(responses)-1); i[0] += 1
            content = responses[idx]
            messages.append({"role": "assistant", "content": content})
            return content
        def parse_action(self, code): return _simple_parse_action(code)
        def render_system_prompt(self, tpl): return "你是子智能体"
        def _run_tool_with_hooks(self, tool_name, args, messages, available_tools=None, cancel_message="操作被用户取消"):
            tool_map = available_tools or {}
            if tool_name not in tool_map:
                observation = f"工具 '{tool_name}' 不存在，可用工具：{', '.join(tool_map.keys())}"
                messages.append({"role": "user", "content": f"<observation>{observation}</observation>"})
                return observation, False
            try:
                observation = tool_map[tool_name](*args)
            except Exception as e:
                observation = f"工具执行错误：{e}"
            messages.append({"role": "user", "content": f"<observation>{observation}</observation>"})
            return observation, False
    return FakeAgent()

def test_final_answer():
    agent = make_mock_agent([
        '<action>read_file("/tmp/a.py")</action>',
        '<final_answer>文件内容是hello</final_answer>',
    ])
    result = SubagentContext("读文件", MOCK_TOOLS, agent, 5).run()
    assert result == "文件内容是hello", f"got: {result}"
    print("✅ final_answer 正确返回")

def test_observation_appended():
    agent = make_mock_agent([
        '<action>read_file("/tmp/a.py")</action>',
        '<final_answer>完成</final_answer>',
    ])
    ctx = SubagentContext("读文件", MOCK_TOOLS, agent, 5)
    ctx.run()
    assert any("<observation>" in m.get("content","") for m in ctx.messages)
    print("✅ observation 正确追加")

def test_tool_not_found():
    agent = make_mock_agent([
        '<action>nonexistent("x")</action>',
        '<final_answer>无法完成</final_answer>',
    ])
    ctx = SubagentContext("测试", MOCK_TOOLS, agent, 5)
    ctx.run()
    assert any("不存在" in m.get("content","") for m in ctx.messages)
    print("✅ 工具不存在时正确反馈")

def test_max_turns():
    agent = make_mock_agent(['<action>read_file("/tmp/a")</action>'] * 20)
    result = SubagentContext("测试", MOCK_TOOLS, agent, 3).run()
    assert "最大轮数" in result, f"got: {result}"
    print("✅ 最大轮数保护生效")

def test_task_not_in_subagent_tools():
    a = ReActAgent.__new__(ReActAgent)
    a.tools = dict(MOCK_TOOLS)
    a.subagent_tools = {n:f for n,f in a.tools.items() if n!="task"}
    a.tools["task"] = a.task
    assert "task" in a.tools and "task" not in a.subagent_tools
    print("✅ task 注册正确，子智能体不含 task")

if __name__ == "__main__":
    test_final_answer()
    test_observation_appended()
    test_tool_not_found()
    test_max_turns()
    test_task_not_in_subagent_tools()
    print("\n🎉 全部通过！")
