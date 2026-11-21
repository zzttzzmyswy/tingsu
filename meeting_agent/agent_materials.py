"""Immutable copies of user-selected files and bounded reads for meeting agents."""

import base64
import hashlib
import json
import mimetypes
import mmap
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


class AgentError(RuntimeError):
    pass


class AgentCancelled(AgentError):
    pass


IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
NATIVE_FILES = {".pdf", ".txt", ".md", ".csv", ".json", ".docx", ".pptx", ".xlsx"}
READ_LIMIT = 64 * 1024
MATERIAL_TOOLS = {"list_attachments", "read_attachment"}


@dataclass(frozen=True)
class Material:
    path: Path
    id: str
    name: str
    size: int

    @property
    def mime(self):
        return mimetypes.guess_type(self.name)[0] or "application/octet-stream"

    def info(self):
        return {"id": self.id, "name": self.name, "size_bytes": self.size, "mime_type": self.mime}


def _extract_text(material, out):
    suffix = Path(material.name).suffix.lower()
    if suffix in {".docx", ".pptx"}:
        with zipfile.ZipFile(material.path) as package:
            names = ["word/document.xml"] if suffix == ".docx" else sorted(
                (n for n in package.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                key=lambda n: int(Path(n).stem[5:]),
            )
            for name in names:
                with package.open(name) as part:
                    for _, element in ElementTree.iterparse(part):
                        if element.tag.endswith("}p"):
                            for node in element.iter():
                                if node.tag.endswith("}t") and node.text:
                                    out.write(node.text)
                            out.write("\n")
                            element.clear()
        return
    if suffix == ".pdf":
        from pypdf import PdfReader
        with material.path.open("rb") as stream:
            for page in PdfReader(stream).pages:
                out.write(page.extract_text() or "")
                out.write("\n")
        return
    with material.path.open("rb") as stream:
        bom = stream.read(3)
    encodings = ("utf-16",) if bom.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
    for encoding in encodings:
        out.seek(0)
        out.truncate()
        try:
            with material.path.open(encoding=encoding, newline="") as source:
                while chunk := source.read(64 * 1024):
                    if "\0" in chunk:
                        raise ValueError("二进制文件可按 base64 读取原件")
                    out.write(chunk)
            return
        except UnicodeDecodeError:
            continue
    raise ValueError("未能解码文字，可按 base64 读取原件")


class MaterialLibrary:
    def __init__(self, directory):
        self.directory = Path(directory)

    def add(self, source):
        source = Path(source).expanduser()
        if not source.is_file():
            raise AgentError(f"找不到会议材料：{source.name}")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Publish the complete original and its name together, including on retries.
        temporary = Path(tempfile.mkdtemp(prefix=".adding-", dir=self.directory))
        try:
            digest = hashlib.sha256(source.name.encode("utf-8") + b"\0")
            size = 0
            with source.open("rb") as origin, (temporary / "original").open("wb") as target:
                while chunk := origin.read(1024 * 1024):
                    digest.update(chunk)
                    target.write(chunk)
                    size += len(chunk)
            (temporary / "name.json").write_text(json.dumps(source.name), encoding="utf-8")
            identifier = digest.hexdigest()
            folder = self.directory / identifier
            try:
                temporary.rename(folder)
            except OSError:
                if not (folder / "original").is_file():
                    raise
            return Material(folder / "original", identifier, source.name, size)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _get(self, identifier):
        if not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise AgentError("请选择本场已发送的附件 ID")
        directory = self.directory / identifier
        try:
            path = directory / "original"
            name = json.loads((directory / "name.json").read_text(encoding="utf-8"))
            return Material(path, identifier, name, path.stat().st_size)
        except FileNotFoundError as exc:
            raise AgentError("本场会议没有该附件") from exc

    def _text_view(self, material):
        view = material.path.with_name("text.utf8")
        if view.is_file():
            return view
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=view.parent, delete=False) as out:
            tmp = Path(out.name)
            try:
                _extract_text(material, out)
            except Exception as exc:
                out.close()
                tmp.unlink(missing_ok=True)
                raise AgentError(f"无法提取 {material.name} 的文字，可按 base64 读取原件。") from exc
        try:
            os.replace(tmp, view)
        finally:
            tmp.unlink(missing_ok=True)
        return view

    def tool(self, name, args):
        if name == "list_attachments":
            ids = sorted(p.name for p in self.directory.glob("*") if re.fullmatch(r"[a-f0-9]{64}", p.name))
            offset = max(0, int(args.get("offset", 0)))
            limit = max(1, min(100, int(args.get("limit", 30))))
            return {"total": len(ids), "items": [self._get(i).info() for i in ids[offset:offset + limit]]}
        if name != "read_attachment":
            raise ValueError("未知附件操作")
        material = self._get(args["id"])
        mode = args["format"]
        if mode not in ("text", "base64"):
            raise ValueError("请选择 text 或 base64")
        path = self._text_view(material) if mode == "text" else material.path
        size = path.stat().st_size
        start = int(args["offset"])
        start = max(0, size + start) if start < 0 else min(size, start)
        length = min(READ_LIMIT, max(4, int(args["limit"])))
        query = args.get("query", "")
        if query and mode != "text":
            raise ValueError("关键词查找请使用 text 格式")
        with path.open("rb") as stream:
            if query:
                match = -1
                if size and start < size:
                    with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                        match = mapped.find(query.encode("utf-8"), start)
                if match < 0:
                    return {"filename": material.name, "found": False, "eof": True, "view_bytes": size}
                start = match
            stream.seek(start)
            data = stream.read(length)
            if mode == "text":
                while data and data[0] & 0xc0 == 0x80:
                    start += 1
                    data = data[1:]
                # Drop only a partial UTF-8 sequence at a page boundary; next_offset resumes before it.
                content = data.decode("utf-8", errors="ignore")
                end = start + len(content.encode("utf-8"))
                if not content and start < size:
                    stream.seek(start)
                    content = stream.read(4).decode("utf-8", errors="ignore")
                    end = start + len(content.encode("utf-8"))
            else:
                content = base64.b64encode(data).decode("ascii")
                end = start + len(data)
        return {"filename": material.name, "format": mode, "offset": start, "next_offset": end,
                "view_bytes": size, "eof": end >= size, "content": content}


def make_inputs(provider, text, attachments, context, *, frame=None):
    responses = provider == "openai"
    text_type = "input_text" if responses else "text"
    request = {"request": text, "meeting_context": context}
    if attachments:
        request["attachments"] = [material.info() for material in attachments]
    inputs = [{"type": text_type, "text": json.dumps(request, ensure_ascii=False)}]
    # Larger files remain available in full through the file tools. Bound only the
    # in-memory/native request, shared across all file and image blocks.
    remaining = 50_000_000 if responses else 24 * 1024 * 1024
    files = [(m.path, m.name, m.mime, m.size) for m in attachments]
    if frame:
        shot = Path(frame)
        files.insert(0, (shot, shot.name, mimetypes.guess_type(shot.name)[0], shot.stat().st_size))
    for path, name, mime, size in files:
        if size > remaining:
            if frame and path == Path(frame):
                raise AgentError("截图超过图像请求容量，请降低截图尺寸。")
            continue
        claude_pdf = provider == "claude" and mime == "application/pdf"
        if mime not in IMAGE_TYPES and not claude_pdf and not (responses and Path(name).suffix.lower() in NATIVE_FILES):
            continue
        remaining -= size
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        url = f"data:{mime};base64,{data}"
        if mime in IMAGE_TYPES:
            inputs.append({"type": "input_image", "image_url": url} if responses else {"type": "image", "url": url})
        elif claude_pdf:
            inputs.append({"type": "document", "title": name,
                           "source": {"type": "base64", "media_type": mime, "data": data}})
        else:
            inputs.append({"type": "input_file", "filename": name, "file_data": url})
    return inputs
