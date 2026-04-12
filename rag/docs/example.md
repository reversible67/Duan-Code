# 项目知识库示例文档

## 项目简介

本项目是一个基于 ReAct（Reasoning + Acting）架构的 AI Agent，使用 Google Gemini 2.5 Flash 作为大语言模型。

## 支持的工具列表

1. **read_file** - 读取指定文件的内容
2. **write_to_file** - 将内容写入指定文件
3. **run_terminal_command** - 在终端执行命令
4. **list_directory** - 列出目录结构
5. **search_in_files** - 在文件中搜索关键词
6. **web_search** - 使用 DuckDuckGo 联网搜索
7. **query_knowledge_base** - 查询本地知识库（RAG）

## 技术栈

- Python 3.11+
- Google Generative AI SDK（google-genai）
- ChromaDB（向量数据库）
- sentence-transformers（本地 Embedding 模型）
- DuckDuckGo Search（ddgs）
- Click（命令行框架）
- httpx（HTTP 客户端，支持代理）

## 使用方法

运行命令：
```
uv run python agent.py <项目目录>
```

然后输入自然语言任务，Agent 会自动选择合适的工具完成任务。

## RAG 知识库使用步骤

1. 将文档（.txt 或 .md 格式）放入 `rag/docs/` 目录
2. 运行 `uv run python rag/build_index.py` 构建向量索引
3. 启动 Agent，直接询问与文档相关的问题

## 注意事项

- 需要在 `.env` 文件中配置 `GOOGLE_API_KEY`
- 中国大陆地区需配置 `HTTPS_PROXY` 环境变量
- 首次构建 RAG 索引时会自动下载 `all-MiniLM-L6-v2` 模型（约 90MB）
