import json
import os
import shutil
import sys
import types
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

TOOLS_STUB = types.ModuleType("tools")


def read_file(file_path):
    return Path(file_path).read_text(encoding="utf-8")



def write_to_file(file_path, content):
    target = Path(file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return "写入成功"



def run_terminal_command(command, timeout=60):
    return f"跳过终端命令：{command}"



def list_directory(path):
    return "\n".join(sorted(item.name for item in Path(path).iterdir()))



def search_in_files(keyword, directory):
    matches = []
    for path in Path(directory).rglob("*"):
        if path.is_file():
            try:
                for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
                    if keyword in line:
                        matches.append(f"{path}:{line_no}:{line}")
            except OSError:
                continue
    return "\n".join(matches) if matches else f"未找到包含 '{keyword}' 的内容"



def web_search(query, max_results=3):
    return f"stub web search: {query}"



def query_knowledge_base(query, top_k=3):
    return f"stub kb search: {query}"


for name, func in {
    "read_file": read_file,
    "write_to_file": write_to_file,
    "run_terminal_command": run_terminal_command,
    "list_directory": list_directory,
    "search_in_files": search_in_files,
    "web_search": web_search,
    "query_knowledge_base": query_knowledge_base,
}.items():
    setattr(TOOLS_STUB, name, func)

sys.modules["tools"] = TOOLS_STUB

from agent import ReActAgent

WORKSPACE_DIR = ROOT_DIR / "test" / ".tmp_repo_intel_live"
SERVER_FILE = ROOT_DIR / "internal_mcp" / "servers" / "repo_intel_server.py"


def build_workspace():
    if WORKSPACE_DIR.exists():
        shutil.rmtree(WORKSPACE_DIR)
    (WORKSPACE_DIR / ".mcp").mkdir(parents=True, exist_ok=True)
    config = {
        "servers": [
            {
                "name": "repo_intel",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(SERVER_FILE)],
                "env": {"MCP_REPO_ROOT": str(ROOT_DIR)},
                "enabled": True,
                "timeout_seconds": 30,
            }
        ]
    }
    (WORKSPACE_DIR / ".mcp" / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def cleanup_workspace():
    if WORKSPACE_DIR.exists():
        shutil.rmtree(WORKSPACE_DIR)


def build_agent():
    return ReActAgent(
        tools=[
            TOOLS_STUB.read_file,
            TOOLS_STUB.write_to_file,
            TOOLS_STUB.run_terminal_command,
            TOOLS_STUB.list_directory,
            TOOLS_STUB.search_in_files,
            TOOLS_STUB.web_search,
            TOOLS_STUB.query_knowledge_base,
        ],
        model=os.getenv("LIVE_MCP_DEMO_MODEL", "qwen2.5:3b"),
        project_directory=str(WORKSPACE_DIR),
    )


def run_live_repo_intel_demo():
    try:
        build_workspace()
        agent = build_agent()
        status = agent.get_status()
        print("# status")
        print(status)

        find_symbol_tool = agent.tools.get("mcp_repo_intel_find_symbol")
        summarize_tool = agent.tools.get("mcp_repo_intel_summarize_module_responsibilities")
        diff_tool = agent.tools.get("mcp_repo_intel_diff_impacted_files")
        if find_symbol_tool is None or summarize_tool is None or diff_tool is None:
            raise AssertionError(f"repo_intel MCP tools 未加载成功，可用工具：{sorted(agent.tools)}")

        symbol_result = json.loads(find_symbol_tool({"symbol_name": "ReActAgent"}))
        summary_result = json.loads(summarize_tool({"file_paths": ["agent.py", "internal_mcp/client.py"]}))
        impact_result = json.loads(diff_tool({"changed_files": ["agent.py"]}))

        print("\n# find_symbol")
        print(json.dumps(symbol_result, ensure_ascii=False, indent=2))
        print("\n# summarize_module_responsibilities")
        print(json.dumps(summary_result, ensure_ascii=False, indent=2))
        print("\n# diff_impacted_files")
        print(json.dumps(impact_result, ensure_ascii=False, indent=2))

        match_paths = {item["path"] for item in symbol_result["matches"]}
        if "agent.py" not in match_paths:
            raise AssertionError(f"find_symbol 未定位到 ReActAgent：{symbol_result}")
        module_paths = {item["path"] for item in summary_result["modules"]}
        if module_paths != {"agent.py", "internal_mcp/client.py"}:
            raise AssertionError(f"模块总结返回异常：{summary_result}")
        related_test_paths = {item["path"] for item in impact_result["related_tests"]}
        if "test/test_agent_mcp.py" not in related_test_paths:
            raise AssertionError(f"diff_impacted_files 未识别相关测试：{impact_result}")
        if "- 已连接 server：1" not in status:
            raise AssertionError(f"MCP 状态异常：{status}")
        return "live repo_intel demo ok"
    finally:
        cleanup_workspace()


if __name__ == "__main__":
    result = run_live_repo_intel_demo()
    print("\n✅", result)
