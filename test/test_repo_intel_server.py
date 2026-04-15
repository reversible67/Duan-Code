import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ["MCP_REPO_ROOT"] = str(Path(__file__).resolve().parent.parent)

from internal_mcp.servers.repo_intel_server import _diff_impacted_files_impl, _find_symbol_impl, _summarize_module_responsibilities_impl



def test_repo_intel_server_core_tools():
    symbol_result = _find_symbol_impl("ReActAgent")
    summary_result = _summarize_module_responsibilities_impl(["agent.py", "internal_mcp/client.py"])
    impact_result = _diff_impacted_files_impl(["agent.py"])

    symbol_paths = {item["path"] for item in symbol_result["matches"]}
    assert "agent.py" in symbol_paths

    summary_paths = {item["path"] for item in summary_result["modules"]}
    assert summary_paths == {"agent.py", "internal_mcp/client.py"}

    related_test_paths = {item["path"] for item in impact_result["related_tests"]}
    assert "test/test_agent_mcp.py" in related_test_paths


if __name__ == "__main__":
    test_repo_intel_server_core_tools()
    print("✅ test_repo_intel_server 通过")
