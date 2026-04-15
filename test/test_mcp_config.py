import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from internal_mcp.config import load_mcp_server_configs


def test_load_mcp_server_configs_reads_enabled_stdio_servers(tmp_path: Path):
    config_dir = tmp_path / ".mcp"
    config_dir.mkdir()
    config_path = config_dir / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "filesystem",
                        "transport": "stdio",
                        "command": "python",
                        "args": ["server.py"],
                        "env": {"DEMO": "1", "MCP_REPO_ROOT": "../"},
                        "enabled": True,
                        "timeout_seconds": 15,
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    configs = load_mcp_server_configs(str(tmp_path))

    assert len(configs) == 1
    assert configs[0].name == "filesystem"
    assert configs[0].command == "python"
    assert configs[0].args == [str((tmp_path / "server.py").resolve())]
    assert configs[0].env == {"DEMO": "1", "MCP_REPO_ROOT": str(tmp_path.resolve())}
    assert configs[0].timeout_seconds == 15


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_load_mcp_server_configs_reads_enabled_stdio_servers(Path(tmp_dir))
    print("✅ test_mcp_config 通过")
