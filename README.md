# Python ReAct Agent 自动化助手

## 项目简介

这是一个从零手写的智能 Agent 系统，基于 **Plan-and-Execute + ReAct（Reasoning and Acting）** 架构，结合 Google Gemini 云端模型与 Ollama 本地模型，能够通过多种工具与环境交互，自动完成文件操作、代码分析、网络搜索等复杂任务。

本项目作为 LLM Agent 学习项目，参考了 [MarkTechStation/VideoCode](https://github.com/MarkTechStation/VideoCode)。

## 核心特性

- 🗺️ **Plan-and-Execute 架构**：先规划任务步骤，再逐步执行，条理清晰
- ⚡ **Skills 插件系统**：预定义技能可通过 `/skill-name` slash 命令或关键词精确触发，跳过 LLM 规划
- 🤖 **多模型支持**：命令行一键切换 Google Gemini API 或 Ollama 本地模型（如 qwen2.5:3b）
- 🧠 **会话级记忆**：同一次会话中保留历史任务上下文，支持多轮对话
- 🔧 **多工具集成**：文件读写、目录列表、代码搜索、终端命令、网页搜索、RAG 知识库查询
- 📦 **历史压缩**：超过阈值自动压缩消息历史，防止上下文溢出
- 🔒 **安全机制**：危险终端命令执行前要求用户确认

## 系统架构

### 核心组件

```
├── agent.py              # 核心 ReActAgent 类（Plan/Execute/ReAct 循环）
├── tools.py              # 工具函数集合
├── skills.py             # Skills 加载与匹配逻辑
├── prompt_template.py    # ReAct 与 Plan 提示词模板
├── skills/               # 技能插件目录
│   ├── analyze-code/     # 代码分析技能
│   │   ├── SKILL.md      # 技能定义（关键词 + 步骤）
│   │   ├── reference.md  # 参考资料
│   │   └── scripts/      # 可选执行脚本
│   ├── list-files/       # 文件列表技能
│   └── write-tests/      # 单元测试编写技能
├── rag/                  # RAG 知识库模块
│   ├── build_index.py    # 构建向量索引
│   └── docs/             # 知识库文档
└── test/                 # 测试文件
```

### 工作流程

1. **用户输入** → 接收任务
2. **Skills 匹配** → slash 命令精确触发 > 关键词模糊匹配 > LLM 规划（三级优先级）
3. **规划阶段** → LLM 生成结构化步骤列表
4. **执行阶段** → 每个步骤进入 ReAct 小循环（Thought → Action → Observation）
5. **工具调用** → 执行具体工具并获取结果
6. **循环或汇总** → 所有步骤完成后 LLM 汇总最终答案

## 安装和配置

### 环境要求

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) 包管理器（推荐）
- Google API Key（云端模型）或 [Ollama](https://ollama.com)（本地模型）
- 使用 Google Gemini 需科学上网

### 安装依赖

```bash
uv sync
```

### 环境变量配置

在项目根目录创建 `.env` 文件：

```
GOOGLE_API_KEY=your_google_api_key_here
HTTPS_PROXY=http://127.0.0.1:7890   # 可选，代理配置
```

谷歌 API 秘钥可在此申请：https://aistudio.google.com/apikey

## 快速开始

### 基础使用（Gemini 云端模型）

```bash
uv run python agent.py <项目目录>
```

### 切换到本地 Ollama 模型

```bash
# 先安装模型
ollama pull qwen2.5:3b

# 启动时指定模型
uv run python agent.py <项目目录> --model qwen2.5:3b
```

### 查看所有启动选项

```bash
uv run python agent.py --help
```

### 使用 Slash 命令

启动后，直接输入 `/skill-name` 精确触发预定义技能：

```
请输入任务：/analyze-code
请输入任务：/list-files
请输入任务：/write-tests
```

也可以使用自然语言，系统会自动关键词匹配：

```
请输入任务：帮我分析一下代码质量
请输入任务：列出项目目录结构
```

## 配置选项

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `project_directory` | 必填 | 项目工作目录（位置参数） |
| `--model` | `gemini-2.5-flash` | 模型名称，`gemini-*` 走 Google API，其他走 Ollama |

Agent 内部可调常量（`agent.py`）：

```python
MAX_HISTORY_MESSAGES = 20   # 单步 ReAct 历史压缩阈值
MAX_SESSION_HISTORY  = 5    # 会话记忆注入条数上限
```

## 内置工具

| 工具 | 说明 |
|------|------|
| `read_file` | 读取文件内容 |
| `write_to_file` | 写入文件 |
| `run_terminal_command` | 执行终端命令（危险操作需确认） |
| `list_directory` | 列出目录结构 |
| `search_in_files` | 在文件中搜索关键词 |
| `web_search` | DuckDuckGo 网页搜索 |
| `query_knowledge_base` | 查询本地 RAG 知识库 |

## 开发和扩展

### 添加新工具

1. 在 `tools.py` 中定义新函数，添加详细 docstring
2. 在 `agent.py` 的 `main()` 中将函数加入 `tools` 列表
3. 工具会自动注册到系统提示词中

```python
def your_new_tool(param1: str, param2: int) -> str:
    """
    工具功能描述

    Args:
        param1: 参数1描述
        param2: 参数2描述

    Returns:
        返回值描述
    """
    # 实现代码
    return "结果"
```

### 添加新技能（Skill）

在 `skills/` 下创建子目录，添加 `SKILL.md`：

```markdown
# skill-name

## Description
技能描述

## Keywords
- 关键词1
- 关键词2

## Steps
1. 第一步
2. 第二步
3. 第三步
```

技能会在下次启动时自动加载，无需修改任何代码。

## 注意事项

1. 使用 Google Gemini 需确保 API Key 已正确配置且网络可访问
2. `run_terminal_command` 执行前会要求用户确认，请仔细核查命令
3. RAG 知识库需先运行 `rag/build_index.py` 构建索引
4. 本地 Ollama 模型（如 qwen2.5:3b）指令遵循能力较弱，复杂任务建议使用 Gemini

## 致谢

本项目的实现受到以下项目和研究的启发：

- 感谢项目教程 [MarkTechStation/VideoCode](https://github.com/MarkTechStation/VideoCode)
- 感谢 Google Gemini API 提供的强大语言模型支持

如果您在使用本项目时发现任何问题或有改进建议，欢迎提交 Issue 或 Pull Request！

## 许可证

本项目基于 MIT 许可证开源，详情请参见 LICENSE 文件。
