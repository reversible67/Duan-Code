import os
import sys
import chromadb
from sentence_transformers import SentenceTransformer

CHROMA_DIR = os.path.join(os.path.dirname(__file__), "chroma_db")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")
COLLECTION_NAME = "knowledge_base"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def load_documents(docs_dir: str) -> list[dict]:
    """读取 docs/ 目录下所有 .txt 和 .md 文件，返回文档列表"""
    documents = []
    for filename in os.listdir(docs_dir):
        if not filename.endswith((".txt", ".md")):
            continue
        file_path = os.path.join(docs_dir, filename)
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        documents.append({"filename": filename, "content": content})
    return documents


def split_into_chunks(content: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """将长文本按字符数切片，相邻片段有重叠"""
    chunks = []
    start = 0
    while start < len(content):
        end = start + chunk_size
        chunks.append(content[start:end])
        start += chunk_size - overlap
    return chunks


def build_index():
    """读取文档、切片、向量化，存入 ChromaDB"""
    if not os.path.exists(DOCS_DIR):
        os.makedirs(DOCS_DIR)
        print(f"已创建 docs 目录：{DOCS_DIR}")
        print("请将你的知识库文档（.txt / .md）放入该目录后重新运行。")
        return

    documents = load_documents(DOCS_DIR)
    if not documents:
        print(f"docs/ 目录中没有找到 .txt 或 .md 文件，请添加后重新运行。")
        return

    print(f"加载了 {len(documents)} 个文档，开始切片...")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    client = chromadb.PersistentClient(path=CHROMA_DIR)

    if COLLECTION_NAME in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION_NAME)
    collection = client.create_collection(COLLECTION_NAME)

    all_chunks = []
    all_ids = []
    all_metas = []

    for doc in documents:
        chunks = split_into_chunks(doc["content"])
        for i, chunk in enumerate(chunks):
            chunk_id = f"{doc['filename']}__chunk{i}"
            all_chunks.append(chunk)
            all_ids.append(chunk_id)
            all_metas.append({"source": doc["filename"], "chunk_index": i})

    print(f"共 {len(all_chunks)} 个文本块，正在向量化（首次运行会下载模型，请稍等）...")
    embeddings = model.encode(all_chunks, show_progress_bar=True).tolist()

    collection.add(
        documents=all_chunks,
        embeddings=embeddings,
        ids=all_ids,
        metadatas=all_metas,
    )

    print(f"\n✅ 索引构建完成！共存入 {len(all_chunks)} 个文本块，保存至 {CHROMA_DIR}")


if __name__ == "__main__":
    build_index()
