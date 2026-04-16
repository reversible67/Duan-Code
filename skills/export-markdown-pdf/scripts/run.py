import html
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def format_inline(text: str) -> str:
    placeholders: list[str] = []

    def hold(match: re.Match[str]) -> str:
        placeholders.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"@@CODE{len(placeholders) - 1}@@"

    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", hold, escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
    escaped = re.sub(r"\[(.+?)\]\((.+?)\)", r"<a href=\"\2\">\1</a>", escaped)

    for index, value in enumerate(placeholders):
        escaped = escaped.replace(f"@@CODE{index}@@", value)
    return escaped


def is_special_block_start(line: str) -> bool:
    stripped = line.strip()
    return (
        not stripped
        or stripped.startswith("```")
        or bool(re.match(r"#{1,6}\s+", stripped))
        or bool(re.match(r"([-*_])\1{2,}$", stripped))
        or stripped.startswith(">")
        or bool(re.match(r"[-*+]\s+", stripped))
        or bool(re.match(r"\d+\.\s+", stripped))
    )


def collect_list(lines: list[str], start: int) -> tuple[str, int]:
    line = lines[start]
    ordered = bool(re.match(r"\s*\d+\.\s+", line))
    pattern = r"\s*\d+\.\s+(.*)" if ordered else r"\s*[-*+]\s+(.*)"
    tag = "ol" if ordered else "ul"
    items: list[str] = []
    index = start
    while index < len(lines):
        current = lines[index]
        match = re.match(pattern, current)
        if not match:
            break
        items.append(f"<li>{format_inline(match.group(1).strip())}</li>")
        index += 1
    return f"<{tag}>\n" + "\n".join(items) + f"\n</{tag}>", index


def collect_blockquote(lines: list[str], start: int) -> tuple[str, int]:
    parts: list[str] = []
    index = start
    while index < len(lines):
        current = lines[index]
        if not current.strip().startswith(">"):
            break
        parts.append(re.sub(r"^\s*>\s?", "", current.rstrip()))
        index += 1
    content = "<br/>\n".join(format_inline(part) for part in parts)
    return f"<blockquote><p>{content}</p></blockquote>", index


def collect_code_block(lines: list[str], start: int) -> tuple[str, int]:
    opening = lines[start].strip()
    language = opening[3:].strip()
    index = start + 1
    parts: list[str] = []
    while index < len(lines) and not lines[index].strip().startswith("```"):
        parts.append(lines[index])
        index += 1
    if index < len(lines):
        index += 1
    class_attr = f' class="language-{html.escape(language)}"' if language else ""
    code = html.escape("\n".join(parts))
    return f"<pre><code{class_attr}>{code}</code></pre>", index


def collect_paragraph(lines: list[str], start: int) -> tuple[str, int]:
    parts: list[str] = []
    index = start
    while index < len(lines) and not is_special_block_start(lines[index]):
        parts.append(lines[index].strip())
        index += 1
    text = " ".join(part for part in parts if part)
    return f"<p>{format_inline(text)}</p>", index


def markdown_to_html(markdown_text: str, title: str) -> str:
    lines = markdown_text.splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            block, index = collect_code_block(lines, index)
            blocks.append(block)
            continue

        heading = re.match(r"(#{1,6})\s+(.*)", stripped)
        if heading:
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{format_inline(heading.group(2).strip())}</h{level}>")
            index += 1
            continue

        if re.match(r"([-*_])\1{2,}$", stripped):
            blocks.append("<hr/>")
            index += 1
            continue

        if stripped.startswith(">"):
            block, index = collect_blockquote(lines, index)
            blocks.append(block)
            continue

        if re.match(r"\s*[-*+]\s+", line) or re.match(r"\s*\d+\.\s+", line):
            block, index = collect_list(lines, index)
            blocks.append(block)
            continue

        block, index = collect_paragraph(lines, index)
        blocks.append(block)

    body = "\n\n".join(blocks)
    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{html.escape(title)}</title>
<style>
body {{
  font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Segoe UI", sans-serif;
  color: #1f2328;
  line-height: 1.75;
  font-size: 14px;
  margin: 0;
  background: #ffffff;
}}
main {{
  max-width: 860px;
  margin: 0 auto;
  padding: 28px 40px 36px;
  box-sizing: border-box;
}}
h1, h2, h3, h4, h5, h6 {{
  font-weight: 700;
  line-height: 1.35;
  margin-top: 1.4em;
  margin-bottom: 0.6em;
  color: #0f172a;
}}
h1 {{ font-size: 28px; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.3em; }}
h2 {{ font-size: 22px; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.25em; }}
h3 {{ font-size: 18px; }}
h4 {{ font-size: 16px; }}
p, ul, ol, blockquote, pre {{ margin-top: 0.7em; margin-bottom: 0.7em; }}
ul, ol {{ padding-left: 1.6em; }}
li {{ margin: 0.3em 0; }}
blockquote {{
  margin-left: 0;
  padding: 0.1em 1em;
  color: #374151;
  border-left: 4px solid #cbd5e1;
  background: #f8fafc;
}}
code {{
  font-family: "Cascadia Code", "Consolas", monospace;
  font-size: 0.92em;
  background: #f6f8fa;
  padding: 0.15em 0.35em;
  border-radius: 4px;
}}
pre {{
  background: #0f172a;
  color: #e5e7eb;
  border-radius: 8px;
  padding: 14px 16px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
}}
pre code {{
  background: transparent;
  color: inherit;
  padding: 0;
  border-radius: 0;
}}
hr {{
  border: none;
  border-top: 1px solid #e5e7eb;
  margin: 1.6em 0;
}}
a {{ color: #0969da; text-decoration: none; }}
strong {{ font-weight: 700; }}
@page {{
  size: A4;
  margin: 18mm 14mm 18mm 14mm;
}}
</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>
'''


def export_pdf_via_word(html_path: Path, pdf_path: Path) -> None:
    env = os.environ.copy()
    env["MARKDOWN_EXPORT_HTML"] = str(html_path)
    env["MARKDOWN_EXPORT_PDF"] = str(pdf_path)
    command = r"""
$ErrorActionPreference = 'Stop'
$html = $env:MARKDOWN_EXPORT_HTML
$pdf = $env:MARKDOWN_EXPORT_PDF
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
$doc = $null
try {
    $doc = $word.Documents.Open($html)
    $doc.ExportAsFixedFormat($pdf, 17)
} finally {
    if ($null -ne $doc) {
        $doc.Close()
    }
    $word.Quit()
}
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=True,
        env=env,
    )


def export_markdown_to_pdf(input_path: Path, pdf_path: Path, keep_html: bool = False) -> tuple[Path, Path | None]:
    markdown_text = input_path.read_text(encoding="utf-8")
    html_text = markdown_to_html(markdown_text, input_path.stem)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    if keep_html:
        html_path = pdf_path.with_suffix(".export.html")
        html_path.write_text(html_text, encoding="utf-8")
    else:
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
        temp_file.close()
        html_path = Path(temp_file.name)
        html_path.write_text(html_text, encoding="utf-8")

    export_pdf_via_word(html_path, pdf_path)

    if not keep_html:
        html_path.unlink(missing_ok=True)
        return pdf_path, None
    return pdf_path, html_path


def main() -> int:
    script_name = Path(sys.argv[0]).name
    if len(sys.argv) < 2:
        print(f"Usage: python {script_name} <input.md> [output.pdf] [--keep-html]")
        return 1

    args = sys.argv[1:]
    keep_html = False
    if "--keep-html" in args:
        keep_html = True
        args.remove("--keep-html")

    if not args:
        print(f"Usage: python {script_name} <input.md> [output.pdf] [--keep-html]")
        return 1

    input_path = Path(args[0]).resolve()
    if len(args) >= 2:
        output_path = Path(args[1]).resolve()
    else:
        output_path = input_path.with_suffix(".pdf")

    pdf_path, html_path = export_markdown_to_pdf(input_path, output_path, keep_html=keep_html)
    print(pdf_path)
    if html_path is not None:
        print(html_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
