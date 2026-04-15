import ast
import json
import os
import re
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("repo_intel")  # 创建一个名叫 repo_intel 的 MCP server 实例

PROJECT_ROOT_ENV = "MCP_REPO_ROOT"
IGNORED_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".idea", ".vscode", "chroma_db"}
TEXT_EXTENSIONS = {".py", ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".sh", ".bat", ".ps1"}


# 定义一个ast遍历器 专门收集python源码中的符号信息
class SymbolCollector(ast.NodeVisitor):
    def __init__(self):
        self.matches: list[dict[str, Any]] = []
        self._class_stack: list[str] = []
        self._scope_stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.matches.append(
            {
                "name": node.name,
                "qualname": ".".join([*self._scope_stack, node.name]) if self._scope_stack else node.name,
                "kind": "class",
                "line": node.lineno,
                "container": ".".join(self._scope_stack) if self._scope_stack else "",
            }
        )
        self._class_stack.append(node.name)
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_callable(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_callable(node, "async_function")

    def _visit_callable(self, node: ast.FunctionDef | ast.AsyncFunctionDef, default_kind: str) -> None:
        kind = "method" if self._class_stack else default_kind
        self.matches.append(
            {
                "name": node.name,
                "qualname": ".".join([*self._scope_stack, node.name]) if self._scope_stack else node.name,
                "kind": kind,
                "line": node.lineno,
                "container": ".".join(self._scope_stack) if self._scope_stack else "",
            }
        )
        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()


def _repo_root() -> Path:
    return Path(os.getenv(PROJECT_ROOT_ENV) or os.getcwd()).resolve()


def _relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(_repo_root()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _iter_repo_files(suffixes: set[str] | None = None):
    root = _repo_root()
    for current_root, dirs, files in os.walk(root, topdown=True):
        dirs[:] = [name for name in dirs if name not in IGNORED_DIRS]
        for file_name in files:
            path = Path(current_root) / file_name
            if suffixes and path.suffix.lower() not in suffixes:
                continue
            yield path.resolve()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _safe_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _resolve_repo_file(file_path: str) -> Path:
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = (_repo_root() / candidate).resolve()
    return candidate


def _python_files() -> list[Path]:
    return sorted(_iter_repo_files({".py"}), key=lambda item: _relative_path(item))


def _collect_symbols(path: Path) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(_read_text(path))
    except SyntaxError:
        return []
    collector = SymbolCollector()
    collector.visit(tree)
    return collector.matches


def _module_name_for_path(path: Path) -> str:
    relative = path.resolve().relative_to(_repo_root())
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative_module(current_module: str, level: int, module: str | None) -> str:
    if level <= 0:
        return module or ""
    current_parts = current_module.split(".") if current_module else []
    if current_parts:
        current_parts = current_parts[:-1]
    prefix_parts = current_parts[: max(0, len(current_parts) - (level - 1))]
    if module:
        prefix_parts.extend(module.split("."))
    return ".".join(part for part in prefix_parts if part)


def _resolve_import_name(import_name: str, module_to_path: dict[str, str]) -> str | None:
    candidate = import_name
    while candidate:
        if candidate in module_to_path:
            return module_to_path[candidate]
        if "." not in candidate:
            break
        candidate = candidate.rsplit(".", 1)[0]
    return None


def _extract_internal_imports(path: Path, module_to_path: dict[str, str]) -> set[str]:
    try:
        tree = ast.parse(_read_text(path))
    except SyntaxError:
        return set()
    current_module = _module_name_for_path(path)
    internal_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolved = _resolve_import_name(alias.name, module_to_path)
                if resolved:
                    internal_imports.add(resolved)
        elif isinstance(node, ast.ImportFrom):
            base_module = _resolve_relative_module(current_module, node.level, node.module)
            resolved_base = _resolve_import_name(base_module, module_to_path) if base_module else None
            if resolved_base:
                internal_imports.add(resolved_base)
            for alias in node.names:
                qualified = ".".join(part for part in [base_module, alias.name] if part)
                resolved_member = _resolve_import_name(qualified, module_to_path) if qualified else None
                if resolved_member:
                    internal_imports.add(resolved_member)
    return internal_imports


def _build_import_graph() -> tuple[dict[str, str], dict[str, set[str]], dict[str, set[str]]]:
    python_files = _python_files()
    module_to_path = {
        _module_name_for_path(path): _relative_path(path)
        for path in python_files
        if _module_name_for_path(path)
    }
    imports_by_path: dict[str, set[str]] = {}
    reverse_imports: dict[str, set[str]] = {}
    for path in python_files:
        relative = _relative_path(path)
        imports = _extract_internal_imports(path, module_to_path)
        imports_by_path[relative] = imports
        for imported in imports:
            reverse_imports.setdefault(imported, set()).add(relative)
    return module_to_path, imports_by_path, reverse_imports


def _collect_reference_matches(symbol_name: str, max_results: int) -> dict[str, Any]:
    pattern = re.compile(rf"\b{re.escape(symbol_name)}\b")
    matches = []
    truncated = False
    for path in _iter_repo_files(TEXT_EXTENSIONS):
        for line_no, line in enumerate(_read_text(path).splitlines(), start=1):
            if not pattern.search(line):
                continue
            matches.append(
                {
                    "path": _relative_path(path),
                    "line": line_no,
                    "text": line.strip(),
                }
            )
            if len(matches) >= max_results:
                truncated = True
                return {"symbol_name": symbol_name, "matches": matches, "truncated": truncated}
    return {"symbol_name": symbol_name, "matches": matches, "truncated": truncated}


def _infer_responsibility(path: Path, symbols: list[dict[str, Any]], internal_imports: set[str]) -> str:
    relative = _relative_path(path)
    class_count = sum(1 for item in symbols if item["kind"] == "class")
    function_count = sum(1 for item in symbols if item["kind"] in {"function", "async_function", "method"})
    imported_modules = sorted(internal_imports)[:4]
    if "test/" in relative or relative.startswith("test_"):
        return f"测试模块，覆盖 {class_count} 个类相关逻辑和 {function_count} 个函数/方法路径。"
    if path.name.endswith("_server.py"):
        return f"MCP server 模块，对外暴露仓库分析能力；内部依赖 {', '.join(imported_modules) or '较少'}。"
    if path.name.endswith("_client.py"):
        return f"客户端/连接层模块，负责对外部能力进行会话调用；内部依赖 {', '.join(imported_modules) or '较少'}。"
    if path.name == "agent.py":
        return f"核心编排模块，负责 Agent 主流程、工具注册和能力装配；内部依赖 {', '.join(imported_modules) or '较少'}。"
    return f"Python 模块，定义 {class_count} 个类与 {function_count} 个函数/方法；内部依赖 {', '.join(imported_modules) or '较少'}。"


def _summarize_single_module(path: Path, imports_by_path: dict[str, set[str]]) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {
            "path": _relative_path(path),
            "exists": False,
            "summary": "文件不存在或不是普通文件",
        }
    symbols = _collect_symbols(path) if path.suffix.lower() == ".py" else []
    internal_imports = imports_by_path.get(_relative_path(path), set())
    top_level_symbols = [item["qualname"] for item in symbols[:8]]
    return {
        "path": _relative_path(path),
        "exists": True,
        "summary": _infer_responsibility(path, symbols, internal_imports),
        "top_level_symbols": top_level_symbols,
        "internal_imports": sorted(internal_imports),
        "line_count": len(_read_text(path).splitlines()),
    }


def _find_tests_for_target(targets: set[str]) -> list[dict[str, Any]]:
    related = []
    for path in _python_files():
        relative = _relative_path(path)
        if not relative.startswith("test/") and not Path(relative).name.startswith("test_"):
            continue
        content = _read_text(path)
        reasons = []
        for target in targets:
            if Path(target).stem in Path(relative).stem:
                reasons.append(f"文件名包含 {Path(target).stem}")
            if re.search(rf"\b{re.escape(Path(target).stem)}\b", content):
                reasons.append(f"内容引用 {Path(target).stem}")
        if reasons:
            related.append({"path": relative, "reason": "；".join(sorted(set(reasons)))})
    related.sort(key=lambda item: item["path"])
    return related


def _find_symbol_impl(symbol_name: str, max_results: int = 20) -> dict[str, Any]:
    matches = []
    for path in _python_files():
        for symbol in _collect_symbols(path):
            if symbol["name"] != symbol_name and symbol["qualname"] != symbol_name:
                continue
            matches.append(
                {
                    "path": _relative_path(path),
                    **symbol,
                }
            )
            if len(matches) >= max_results:
                return {"symbol_name": symbol_name, "matches": matches, "truncated": True}
    return {"symbol_name": symbol_name, "matches": matches, "truncated": False}


def _summarize_module_responsibilities_impl(file_paths: list[str]) -> dict[str, Any]:
    _, imports_by_path, _ = _build_import_graph()
    summaries = [_summarize_single_module(_resolve_repo_file(file_path), imports_by_path) for file_path in file_paths]
    return {"repo_root": _repo_root().as_posix(), "modules": summaries}


def _diff_impacted_files_impl(changed_files: list[str], max_results: int = 20) -> dict[str, Any]:
    _, imports_by_path, reverse_imports = _build_import_graph()
    resolved_changed = []
    missing_files = []
    for item in changed_files:
        resolved = _resolve_repo_file(item)
        relative = _relative_path(resolved)
        if resolved.exists() and resolved.is_file():
            resolved_changed.append(relative)
        else:
            missing_files.append(item)
    direct_dependents: dict[str, set[str]] = {}
    transitive_dependents: dict[str, set[str]] = {}
    supporting_modules: dict[str, list[str]] = {}
    for changed in resolved_changed:
        supporting_modules[changed] = sorted(imports_by_path.get(changed, set()))
        for dependent in reverse_imports.get(changed, set()):
            direct_dependents.setdefault(dependent, set()).add(f"imports {changed}")
        frontier = set(reverse_imports.get(changed, set()))
        visited = set(frontier)
        for _ in range(2):
            next_frontier = set()
            for current in frontier:
                for dependent in reverse_imports.get(current, set()):
                    if dependent in visited or dependent in resolved_changed:
                        continue
                    transitive_dependents.setdefault(dependent, set()).add(f"indirectly depends on {changed} via {current}")
                    next_frontier.add(dependent)
                    visited.add(dependent)
            frontier = next_frontier
            if not frontier:
                break
    related_tests = _find_tests_for_target(set(resolved_changed) | set(direct_dependents))
    direct_items = [
        {"path": path, "reasons": sorted(reasons)}
        for path, reasons in sorted(direct_dependents.items(), key=lambda item: item[0])[:max_results]
    ]
    transitive_items = [
        {"path": path, "reasons": sorted(reasons)}
        for path, reasons in sorted(transitive_dependents.items(), key=lambda item: item[0])[:max_results]
    ]
    return {
        "repo_root": _repo_root().as_posix(),
        "changed_files": resolved_changed,
        "missing_files": missing_files,
        "supporting_modules": supporting_modules,
        "direct_dependents": direct_items,
        "transitive_dependents": transitive_items,
        "related_tests": related_tests[:max_results],
    }


# 这里非常重要  把"仓库分析能力"封装在独立 server 进程里，对外只暴露标准 tool 接口
@mcp.tool()
def find_symbol(symbol_name: str, max_results: int = 20) -> str:
    return _safe_json(_find_symbol_impl(symbol_name, max_results))


@mcp.tool()
def find_references(symbol_name: str, max_results: int = 40) -> str:
    return _safe_json(_collect_reference_matches(symbol_name, max_results))


@mcp.tool()
def summarize_module_responsibilities(file_paths: list[str]) -> str:
    return _safe_json(_summarize_module_responsibilities_impl(file_paths))


@mcp.tool()
def diff_impacted_files(changed_files: list[str], max_results: int = 20) -> str:
    return _safe_json(_diff_impacted_files_impl(changed_files, max_results))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
