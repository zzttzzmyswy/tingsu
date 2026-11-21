"""Read selected Office package parts on demand for agents without a native Office reader."""

from itertools import islice
import posixpath
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile


OFFICE_TOOLS = {
    "inspect_office": (
        "按工作目录中的真实路径查看 XLSX、DOCX、PPTX 原件。section 留空列出工作表、幻灯片或正文；"
        "再指定 section 名称、从零开始的 offset 和本页 limit 读取单元格或段落。Excel 返回坐标、值及已保存的公式。",
        {"path": {"type": "string"}, "section": {"type": "string"},
         "offset": {"type": "integer"}, "limit": {"type": "integer"}},
    )
}


def _relations(package, main):
    parent, filename = posixpath.split(main)
    rels = ET.fromstring(package.read(posixpath.join(parent, "_rels", filename + ".rels")))
    return {
        rel.attrib["Id"]: posixpath.normpath(posixpath.join(parent, rel.attrib["Target"])).lstrip("/")
        for rel in rels if rel.get("TargetMode") != "External"
    }


def _parts(package, extension):
    if extension == ".docx":
        return {"document": "word/document.xml"}
    spreadsheet = extension == ".xlsx"
    main = "xl/workbook.xml" if spreadsheet else "ppt/presentation.xml"
    root = ET.fromstring(package.read(main))
    paths = _relations(package, main)
    parts = {}
    for index, item in enumerate(root.iterfind(".//{*}" + ("sheet" if spreadsheet else "sldId")), 1):
        relationship = next((value for key, value in item.attrib.items() if key.endswith("}id")), None)
        if relationship in paths:
            section = item.attrib["name"] if spreadsheet else str(index)
            parts[section] = paths[relationship]
    return parts


def _texts(element):
    return "".join(n.text or "" for n in element.iterfind(".//{*}t"))


def _items(package, part, spreadsheet):
    with package.open(part) as source:
        for _, node in ET.iterparse(source):
            if spreadsheet and node.tag.endswith("}c"):
                kind = node.get("t", "n")
                value = _texts(node) if kind == "inlineStr" else node.findtext("{*}v", default="")
                formula = node.findtext("{*}f")
                item = {"cell": node.get("r", ""), "value": value}
                if kind == "s" and value.isdigit():
                    item["shared_index"] = int(value)
                if formula is not None:
                    item["formula"] = formula
                if value or formula is not None:
                    yield item
                node.clear()
            elif spreadsheet and node.tag.endswith("}row"):
                node.clear()
            elif not spreadsheet and node.tag.endswith("}p"):
                yield {"text": _texts(node)}
                node.clear()


def _resolve_strings(package, part, items):
    wanted = {item["shared_index"] for item in items if "shared_index" in item}
    if not wanted:
        return
    values = {}
    with package.open(part) as stream:
        index = 0
        for _, node in ET.iterparse(stream):
            if node.tag.endswith("}si"):
                if index in wanted:
                    values[index] = _texts(node)
                    if len(values) == len(wanted):
                        break
                node.clear()
                index += 1
    for item in items:
        if "shared_index" in item:
            item["value"] = values[item.pop("shared_index")]


def inspect_office(workspace, args):
    candidate = workspace / args["path"]
    if not candidate.is_relative_to(workspace):
        raise ValueError("请选择会议工作目录中的文件")
    path = candidate.resolve(strict=True)
    extension = path.suffix.lower()
    if not path.is_relative_to(workspace) or not path.is_file():
        raise ValueError("请选择会议工作目录中的文件")
    if extension not in (".xlsx", ".docx", ".pptx"):
        raise ValueError("支持 XLSX、DOCX 和 PPTX 文件")
    section = args["section"]
    offset = max(0, args["offset"])
    limit = max(1, min(200, args["limit"]))
    try:
        with ZipFile(path) as package:
            parts = _parts(package, extension)
            if not section:
                return {"sections": list(parts)}
            if section not in parts:
                raise ValueError("请选择列出的 section 名称")
            with_items = _items(package, parts[section], extension == ".xlsx")
            try:
                page = list(islice(with_items, offset, offset + limit + 1))
            finally:
                with_items.close()
            more = len(page) > limit
            if more:
                page.pop()
            if extension == ".xlsx" and any("shared_index" in cell for cell in page):
                with package.open("xl/_rels/workbook.xml.rels") as stream:
                    rels = ET.parse(stream).getroot()
                target = next(rel.attrib["Target"] for rel in rels
                              if rel.get("Type", "").endswith("/sharedStrings") and rel.get("TargetMode") != "External")
                part = posixpath.normpath(posixpath.join("xl", target)).lstrip("/")
                _resolve_strings(package, part, page)
            return {"section": section, "items": page, "next_offset": offset + len(page) if more else None}
    except (BadZipFile, ET.ParseError, KeyError, StopIteration) as exc:
        raise ValueError("无法解析此 Office 文件，请检查原件是否完整或已加密") from exc
