import os
import subprocess
from ddgs import DDGS
import chromadb
from sentence_transformers import SentenceTransformer


MAX_READ_SIZE = 100 * 1024  # 100KB，超过此大小的文件只读取前部分

IGNORED_DIRS = {'.git', '.venv', '__pycache__', 'node_modules', '.idea', '.vscode', 'chroma_db'}

def read_file(file_path):
    """用于读取文件内容（超过100KB的文件只读取前部分）"""
    file_size = os.path.getsize(file_path)
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read(MAX_READ_SIZE)
    if file_size > MAX_READ_SIZE:
        content += f"\n\n... [文件过大，仅显示前 {MAX_READ_SIZE // 1024}KB，完整文件约 {file_size // 1024}KB]"
    return content


def write_to_file(file_path, content):
    """将指定内容写入指定文件"""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content.replace("\\n", "\n"))
    return "写入成功"


def run_terminal_command(command, timeout: int = 60):
    """用于执行终端命令，timeout 为超时秒数（默认60秒）"""
    try:
        run_result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
        return "执行成功" if run_result.returncode == 0 else run_result.stderr
    except subprocess.TimeoutExpired:
        return f"命令执行超时（>{timeout}s），已终止"


def list_directory(path):
    """列出指定目录下的所有文件和子目录（自动排除 .git/.venv 等目录）"""
    result = []
    for root, dirs, files in os.walk(path, topdown=True):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        level = root.replace(path, "").count(os.sep)
        indent = "  " * level
        result.append(f"{indent}{os.path.basename(root)}/")
        sub_indent = "  " * (level + 1)
        for file in files:
            result.append(f"{sub_indent}{file}")
    return "\n".join(result) if result else "目录为空"


def search_in_files(keyword, directory):
    """在指定目录下的所有文件中搜索包含关键词的行（自动排除 .git/.venv 等目录）"""
    matches = []
    for root, dirs, files in os.walk(directory, topdown=True):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for file in files:
            file_path = os.path.join(root, file)
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line_num, line in enumerate(f, start=1):
                        if keyword in line:
                            matches.append(f"{file_path}:{line_num}: {line.rstrip()}")
            except (PermissionError, IsADirectoryError):
                continue
    return "\n".join(matches) if matches else f"未找到包含 '{keyword}' 的内容"


def web_search(query: str, max_results: int = 3) -> str:
    """联网搜索工具（ddgs），无需API Key，国内可直接使用"""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))

        if not results:
            return "搜索结果为空"

        output = []
        for idx, res in enumerate(results, 1):
            title = res.get("title", "无标题")
            body = res.get("body", "无摘要")
            output.append(f"【结果{idx}】\n标题：{title}\n内容：{body}\n")

        return "\n".join(output)

    except Exception as e:
        return f"搜索失败：{str(e)}"


_rag_model = None
_rag_collection = None

def _get_rag_components():
    """懒加载 RAG 模型和 ChromaDB collection，避免每次调用都重新初始化"""
    global _rag_model, _rag_collection
    if _rag_model is None:
        _rag_model = SentenceTransformer("all-MiniLM-L6-v2")
    if _rag_collection is None:
        chroma_dir = os.path.join(os.path.dirname(__file__), "rag", "chroma_db")
        client = chromadb.PersistentClient(path=chroma_dir)
        _rag_collection = client.get_collection("knowledge_base")
    return _rag_model, _rag_collection


def query_knowledge_base(question: str, top_k: int = 3) -> str:
    """查询本地知识库，返回与问题最相关的文档片段（需先运行 rag/build_index.py 构建索引）"""
    try:
        model, collection = _get_rag_components()
        embedding = model.encode([question]).tolist()
        results = collection.query(query_embeddings=embedding, n_results=top_k)
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]

        if not documents:
            return "知识库中未找到相关内容"

        output = []
        for i, (doc, meta) in enumerate(zip(documents, metadatas), 1):
            source = meta.get("source", "未知来源")
            output.append(f"【片段{i}】来源：{source}\n{doc}")

        return "\n\n".join(output)

    except Exception as e:
        return f"知识库查询失败：{str(e)}（请先运行 uv run python rag/build_index.py 构建索引）"
