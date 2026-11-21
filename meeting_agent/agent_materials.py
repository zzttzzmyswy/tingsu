"""Translate explicitly selected files into model inputs; never offer a filesystem tool."""

import base64
import json
import mimetypes
import zipfile
from pathlib import Path
from xml.etree import ElementTree


class AgentError(RuntimeError):
    pass


class AgentCancelled(AgentError):
    pass


def _office_text(path):
    with zipfile.ZipFile(path) as package:
        if path.suffix.lower() == ".docx":
            parts = ["word/document.xml"]
        else:
            parts = sorted(
                (
                    n
                    for n in package.namelist()
                    if n.startswith("ppt/slides/slide")
                    and n.removeprefix("ppt/slides/slide").removesuffix(".xml").isdigit()
                ),
                key=lambda n: int(n.removeprefix("ppt/slides/slide").removesuffix(".xml")),
            )
        texts = []
        total_size = 0
        for name in parts:
            total_size += package.getinfo(name).file_size
            if total_size > 20 * 1024 * 1024:
                raise AgentError("文档解压后较大，请选择与本次讨论相关的章节。")
            root = ElementTree.fromstring(package.read(name))
            for paragraph in root.iter():
                if paragraph.tag.endswith("}p"):
                    texts.append(
                        "".join(node.text or "" for node in paragraph.iter() if node.tag.endswith("}t"))
                    )
        return "\n".join(texts)


def _text_from_file(path):
    ext = path.suffix.lower()
    if ext in {".docx", ".pptx"}:
        return _office_text(path)
    if ext == ".pdf":
        from pypdf import PdfReader

        try:
            return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages).strip()
        except Exception as exc:
            raise AgentError(f"无法提取 PDF 文字：{path.name}") from exc
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            content = raw.decode(encoding)
            if "\x00" not in content:
                return content
        except UnicodeError:
            pass
    raise AgentError(f"无法作为文字读取 {path.name}。请选择文本、PDF、DOCX、PPTX 或图片。")


def make_inputs(provider, text, attachments, context):
    # All local protocols consume common text/image blocks. Responses additionally
    # supports native PDF uploads and names its input blocks differently.
    responses_api = provider == "openai"
    inputs = []

    def add_text(value):
        inputs.append({"type": "input_text" if responses_api else "text", "text": value})

    add_text(json.dumps({"request": text, "meeting_context": context}, ensure_ascii=False))
    total_bytes = 0
    total_chars = 0
    for attachment in attachments:
        path = Path(attachment).expanduser().absolute()
        try:
            if not path.is_file():
                raise AgentError(f"找不到会议材料：{path.name}")
            total_bytes += path.stat().st_size
            if total_bytes > 24 * 1024 * 1024:
                raise AgentError("本次附件超过 24 MiB，请分次发送。")
            mime = mimetypes.guess_type(path.name)[0]
            if mime in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                data = base64.b64encode(path.read_bytes()).decode("ascii")
                url = f"data:{mime};base64,{data}"
                add_text(json.dumps({"image_attachment": path.name}, ensure_ascii=False))
                inputs.append(
                    {"type": "image", "url": url}
                    if not responses_api
                    else {"type": "input_image", "image_url": url}
                )
            elif responses_api and path.suffix.lower() == ".pdf":
                data = base64.b64encode(path.read_bytes()).decode("ascii")
                inputs.append(
                    {
                        "type": "input_file",
                        "filename": path.name,
                        "file_data": f"data:application/pdf;base64,{data}",
                    }
                )
            else:
                content = _text_from_file(path)
                if not content.strip():
                    raise AgentError(f"材料中没有可读取的文字：{path.name}。扫描件可作为图片发送。")
                total_chars += len(content)
                if total_chars > 160_000:
                    raise AgentError("附件文字超过 16 万字符，请分次发送相关章节。")
                add_text(json.dumps({"attachment": path.name, "content": content}, ensure_ascii=False))
        except (
            OSError,
            KeyError,
            ValueError,
            ElementTree.ParseError,
            zipfile.BadZipFile,
        ) as exc:
            raise AgentError(f"读取材料失败：{path.name}（{type(exc).__name__}）") from exc
    return inputs
