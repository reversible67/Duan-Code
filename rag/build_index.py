import os

import chromadb
from sentence_transformers import SentenceTransformer

try:
    from .document_pipeline import build_chunks, load_documents
except ImportError:
    from document_pipeline import build_chunks, load_documents

CHROMA_DIR = os.path.join(os.path.dirname(__file__), "chroma_db")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")
COLLECTION_NAME = "knowledge_base"
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def build_index():
    """读取文档、结构化分块、向量化，存入 ChromaDB"""
    if not os.path.exists(DOCS_DIR):
        os.makedirs(DOCS_DIR)
        print(f"已创建 docs 目录：{DOCS_DIR}")
        print("请将你的知识库文档（.txt / .md / .docx / .pdf）放入该目录后重新运行。")
        return

    documents = load_documents(DOCS_DIR)
    if not documents:
        print(f"docs/ 目录中没有找到可索引文档（支持 .txt / .md / .docx / .pdf），请添加后重新运行。")
        return

    print(f"递归加载了 {len(documents)} 个文档，开始结构化分块...")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    client = chromadb.PersistentClient(path=CHROMA_DIR)

    if COLLECTION_NAME in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION_NAME)
    collection = client.create_collection(COLLECTION_NAME)

    chunks = build_chunks(documents, CHUNK_SIZE, CHUNK_OVERLAP)
    if not chunks:
        print("未生成可用文本块，请检查文档内容是否为空或解析失败。")
        return

    all_chunks = [chunk["content"] for chunk in chunks]
    embedding_inputs = [chunk["embedding_text"] for chunk in chunks]
    all_ids = [chunk["id"] for chunk in chunks]
    all_metas = [chunk["metadata"] for chunk in chunks]

    print(f"共 {len(all_chunks)} 个文本块，正在向量化（首次运行会下载模型，请稍等）...")
    embeddings = model.encode(embedding_inputs, show_progress_bar=True).tolist()

    collection.add(
        documents=all_chunks,
        embeddings=embeddings,
        ids=all_ids,
        metadatas=all_metas,
    )

    print(f"\n✅ 索引构建完成！共导入 {len(documents)} 个文档，存入 {len(all_chunks)} 个文本块，保存至 {CHROMA_DIR}")


if __name__ == "__main__":
    build_index()
