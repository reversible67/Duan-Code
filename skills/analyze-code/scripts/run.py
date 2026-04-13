# analyze-code skill entry point
# 当 agent 执行此技能时，可直接调用此脚本进行静态分析（可选扩展）

import ast
import os


def analyze_file(filepath: str) -> dict:
    """静态分析单个 Python 文件，返回结构摘要"""
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)
    functions = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    lines = source.splitlines()

    return {
        "file": filepath,
        "lines": len(lines),
        "functions": functions,
        "classes": classes,
    }


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    for root, _, files in os.walk(target):
        for f in files:
            if f.endswith(".py"):
                result = analyze_file(os.path.join(root, f))
                print(result)
