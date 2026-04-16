---
name: export-markdown-pdf
description: 将 Markdown 文档导出为中文不乱码、格式尽量保持一致的 PDF
keywords:
  - markdown转pdf
  - markdown 转 pdf
  - md转pdf
  - md 转 pdf
  - 导出pdf
  - 转pdf
  - markdown pdf
  - export markdown pdf
  - pdf导出
---

# export-markdown-pdf

## Description
将 Markdown 文档导出为中文不乱码、格式尽量保持一致的 PDF。

## Keywords
- markdown转pdf
- markdown 转 pdf
- md转pdf
- md 转 pdf
- 导出pdf
- 转pdf
- markdown pdf
- export markdown pdf
- pdf导出

## When To Use
- 需要把项目中的 `*.md` 文档导出为 PDF
- 需要尽量保留标题、列表、引用、代码块等 Markdown 样式
- 需要避免中文乱码
- 当前环境是 Windows，并且可使用 Word COM 导出 PDF

## Script
- 脚本路径：`skills/export-markdown-pdf/scripts/run.py`
- 在 Agent 的 `<action>` 中，必须通过工具调用执行脚本，不要直接输出 shell 命令。
- 推荐调用格式：`run_terminal_command("python skills/export-markdown-pdf/scripts/run.py \"<input.md>\" \"<output.pdf>\"")`

## Steps
1. 先确认要导出的 Markdown 文件路径存在，并使用绝对路径。
2. 如果当前是在 Agent 的 `<action>` 中执行，必须写成函数调用，例如 `run_terminal_command("python skills/export-markdown-pdf/scripts/run.py \"<input.md>\" \"<output.pdf>\"")`。
3. 不要在 `<action>` 中直接写 `python ...`、不要输出代码块、不要输出自然语言说明。
4. 如果需要保留中间 HTML 以便排查样式问题，可在命令字符串末尾追加 `--keep-html`。
5. 导出完成后，确认 PDF 文件已生成，并告知用户输出路径。

## Notes
- 脚本内部流程是：Markdown -> UTF-8 HTML -> Word 导出 PDF。
- 这种方式在当前项目环境下更适合处理中文，且能较好保留现有格式。
- 如果用户只要求导出 PDF，默认不保留中间 HTML；只有在需要排查样式时才使用 `--keep-html`。
- 如果需要执行终端命令，`<action>` 的内容必须是 `run_terminal_command(...)`，而不是裸的 shell 命令。

## Examples
```text
run_terminal_command("python skills/export-markdown-pdf/scripts/run.py \"<input.md>\"")
run_terminal_command("python skills/export-markdown-pdf/scripts/run.py \"<input.md>\" \"<output.pdf>\"")
run_terminal_command("python skills/export-markdown-pdf/scripts/run.py \"<input.md>\" --keep-html")
```
