from __future__ import annotations

import re
from pathlib import Path

SUPPORTED_EXTENSIONS = {".md", ".txt", ".docx", ".pdf"}


def load_documents(docs_dir: str) -> list[dict]:
    root = Path(docs_dir)
    documents: list[dict] = []
    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file() or file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        document = _parse_document(file_path, root)
        if document["blocks"]:
            documents.append(document)
    return documents


def build_chunks(documents: list[dict], chunk_size: int, overlap: int) -> list[dict]:
    chunks: list[dict] = []
    for document in documents:
        chunks.extend(_chunk_document(document, chunk_size, overlap))
    return chunks


def _parse_document(file_path: Path, root: Path) -> dict:
    suffix = file_path.suffix.lower()
    relative_path = file_path.relative_to(root).as_posix()
    if suffix == ".md":
        title, blocks = _parse_markdown(file_path)
    elif suffix == ".txt":
        title, blocks = _parse_text(file_path)
    elif suffix == ".docx":
        title, blocks = _parse_docx(file_path)
    elif suffix == ".pdf":
        title, blocks = _parse_pdf(file_path)
    else:
        raise ValueError(f"不支持的文档类型：{suffix}")

    return {
        "source": file_path.name,
        "source_path": relative_path,
        "file_type": suffix.lstrip("."),
        "title": title or file_path.stem,
        "blocks": blocks,
    }


def _parse_markdown(file_path: Path) -> tuple[str, list[dict]]:
    content = file_path.read_text(encoding="utf-8", errors="ignore")
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    title = file_path.stem
    blocks: list[dict] = []
    heading_stack: list[str] = []
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        text = _normalize_text("\n".join(paragraph_lines))
        if text:
            blocks.append({"text": text, "heading_path": " / ".join(heading_stack) or title, "page_number": None})
        paragraph_lines = []

    for raw_line in lines:
        stripped = raw_line.strip()
        heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading_match:
            flush_paragraph()
            level = len(heading_match.group(1))
            heading = heading_match.group(2).strip()
            if level == 1:
                title = heading or title
            heading_stack[:] = heading_stack[: level - 1]
            heading_stack.append(heading)
            continue
        if stripped:
            paragraph_lines.append(raw_line)
        else:
            flush_paragraph()
    flush_paragraph()
    return title, blocks


def _parse_text(file_path: Path) -> tuple[str, list[dict]]:
    content = file_path.read_text(encoding="utf-8", errors="ignore")
    title = file_path.stem
    parts = [segment for segment in re.split(r"\n\s*\n+", content.replace("\r\n", "\n").replace("\r", "\n")) if _normalize_text(segment)]
    blocks = [{"text": _normalize_text(part), "heading_path": title, "page_number": None} for part in parts]
    return title, blocks


def _parse_docx(file_path: Path) -> tuple[str, list[dict]]:
    try:
        from docx import Document as WordDocument
    except ImportError as exc:
        raise RuntimeError("缺少 python-docx 依赖，请先安装后再构建知识库") from exc

    document = WordDocument(str(file_path))
    title = file_path.stem
    blocks: list[dict] = []
    heading_stack: list[str] = []

    for paragraph in document.paragraphs:
        text = _normalize_text(paragraph.text)
        if not text:
            continue
        style_name = ""
        if paragraph.style is not None and paragraph.style.name:
            style_name = paragraph.style.name.strip()
        if style_name.lower().startswith("heading"):
            level_match = re.search(r"(\d+)", style_name)
            level = int(level_match.group(1)) if level_match else 1
            if level == 1 and title == file_path.stem:
                title = text
            heading_stack[:] = heading_stack[: level - 1]
            heading_stack.append(text)
            continue
        blocks.append({"text": text, "heading_path": " / ".join(heading_stack) or title, "page_number": None})

    return title, blocks


def _parse_pdf(file_path: Path) -> tuple[str, list[dict]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("缺少 pypdf 依赖，请先安装后再构建知识库") from exc

    reader = PdfReader(str(file_path))
    title = file_path.stem
    metadata = reader.metadata
    if metadata and metadata.title:
        metadata_title = _normalize_text(str(metadata.title))
        if metadata_title:
            title = metadata_title

    blocks: list[dict] = []
    heading_stack: list[str] = []
    for page_index, page in enumerate(reader.pages, start=1):
        raw_text = page.extract_text() or ""
        for unit in _split_pdf_units(raw_text):
            if _looks_like_heading(unit):
                heading_stack[:] = heading_stack[:1]
                heading_stack.append(unit)
                continue
            blocks.append({
                "text": unit,
                "heading_path": " / ".join(heading_stack) or title,
                "page_number": page_index,
            })

    return title, blocks


def _split_pdf_units(raw_text: str) -> list[str]:
    lines = [line.strip() for line in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]
    units: list[str] = []
    current: list[str] = []
    for line in lines:
        if _looks_like_heading(line):
            if current:
                text = _normalize_text(" ".join(current))
                if text:
                    units.append(text)
                current = []
            units.append(_normalize_text(line))
            continue
        current.append(line)
        if re.search(r"[。！？.!?：:]$", line):
            text = _normalize_text(" ".join(current))
            if text:
                units.append(text)
            current = []
    if current:
        text = _normalize_text(" ".join(current))
        if text:
            units.append(text)
    return units


def _looks_like_heading(text: str) -> bool:
    stripped = _normalize_text(text)
    if not stripped or len(stripped) > 80:
        return False
    if re.match(r"^(第[一二三四五六七八九十\d]+[章节部分]|[0-9]+(?:\.[0-9]+)*|[A-Z][A-Z\s\-]{2,})", stripped):
        return True
    return stripped.endswith(":") or stripped.endswith("：")


def _chunk_document(document: dict, chunk_size: int, overlap: int) -> list[dict]:
    blocks = document["blocks"]
    chunks: list[dict] = []
    current_blocks: list[dict] = []
    current_length = 0
    chunk_index = 0

    for block in blocks:
        text = block["text"]
        if current_blocks and block.get("heading_path") != current_blocks[-1].get("heading_path"):
            chunks.append(_make_chunk(document, current_blocks, chunk_index))
            chunk_index += 1
            current_blocks = []
            current_length = 0

        if len(text) > chunk_size:
            if current_blocks:
                chunks.append(_make_chunk(document, current_blocks, chunk_index))
                chunk_index += 1
                current_blocks = []
                current_length = 0
            for piece in _split_large_text(text, chunk_size, overlap):
                oversized_block = dict(block)
                oversized_block["text"] = piece
                chunks.append(_make_chunk(document, [oversized_block], chunk_index))
                chunk_index += 1
            current_blocks = []
            current_length = 0
            continue

        projected_length = current_length + len(text) + (2 if current_blocks else 0)
        if current_blocks and projected_length > chunk_size:
            chunks.append(_make_chunk(document, current_blocks, chunk_index))
            chunk_index += 1
            current_blocks = _overlap_seed(current_blocks, overlap)
            current_length = _blocks_length(current_blocks)

        current_blocks.append(block)
        current_length = _blocks_length(current_blocks)

    if current_blocks:
        chunks.append(_make_chunk(document, current_blocks, chunk_index))
    return chunks


def _make_chunk(document: dict, blocks: list[dict], chunk_index: int) -> dict:
    content = "\n\n".join(block["text"] for block in blocks if block["text"])
    heading_path = next((block["heading_path"] for block in blocks if block.get("heading_path")), document["title"])
    page_numbers = [block["page_number"] for block in blocks if block.get("page_number") is not None]
    page_start = min(page_numbers) if page_numbers else None
    page_end = max(page_numbers) if page_numbers else None
    embedding_parts = [f"标题：{document['title']}"]
    if heading_path:
        embedding_parts.append(f"章节：{heading_path}")
    embedding_parts.append(f"内容：{content}")
    source_key = re.sub(r"[^a-zA-Z0-9_-]", "_", document["source_path"])
    metadata = {
        "source": document["source"],
        "source_path": document["source_path"],
        "file_type": document["file_type"],
        "title": document["title"],
        "heading_path": heading_path,
        "chunk_index": chunk_index,
        "page_start": page_start,
        "page_end": page_end,
    }
    return {
        "id": f"{source_key}__chunk{chunk_index}",
        "content": content,
        "embedding_text": "\n".join(embedding_parts),
        "metadata": {key: value for key, value in metadata.items() if value is not None},
    }


def _split_large_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    units = _split_semantic_units(text)
    if len(units) > 1:
        pieces: list[str] = []
        current_units: list[str] = []
        current_length = 0
        for unit in units:
            projected_length = current_length + len(unit) + (1 if current_units else 0)
            if current_units and projected_length > chunk_size:
                pieces.append(" ".join(current_units))
                current_units = _overlap_units(current_units, overlap)
                current_length = len(" ".join(current_units))
            current_units.append(unit)
            current_length = len(" ".join(current_units))
        if current_units:
            pieces.append(" ".join(current_units))
        if pieces:
            return pieces

    pieces: list[str] = []
    start = 0
    step = max(chunk_size - overlap, 1)
    while start < len(text):
        end = start + chunk_size
        pieces.append(text[start:end])
        start += step
    return pieces


def _overlap_seed(blocks: list[dict], overlap: int) -> list[dict]:
    if overlap <= 0:
        return []
    seed: list[dict] = []
    total = 0
    for block in reversed(blocks):
        seed.insert(0, block)
        total += len(block["text"])
        if total >= overlap:
            break
    return seed


def _blocks_length(blocks: list[dict]) -> int:
    if not blocks:
        return 0
    return sum(len(block["text"]) for block in blocks) + (len(blocks) - 1) * 2


def _split_semantic_units(text: str) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []
    units = [part.strip() for part in re.split(r"(?<=[。！？.!?；;])\s+|\n+", normalized) if part.strip()]
    return units or [normalized]


def _overlap_units(units: list[str], overlap: int) -> list[str]:
    if overlap <= 0:
        return []
    seed: list[str] = []
    total = 0
    for unit in reversed(units):
        seed.insert(0, unit)
        total += len(unit)
        if total >= overlap:
            break
    return seed


def _normalize_text(text: str) -> str:
    normalized = text.replace("\u3000", " ").replace("\xa0", " ")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()
