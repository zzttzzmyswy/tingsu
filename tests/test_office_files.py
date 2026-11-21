import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from meeting_agent.office_files import inspect_office


class OfficeFilesTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name) / "会议"
        self.work.mkdir()

    def read(self, name, section="", offset=0, limit=20):
        return inspect_office(self.work, dict(path=name, section=section, offset=offset, limit=limit))

    def test_read_xlsx_parts_inline_shared_strings_formula_and_pagination(self):
        with ZipFile(self.work / "账本.xlsx", "w") as z:
            z.writestr("xl/workbook.xml", '<workbook xmlns="urn:xl" xmlns:r="urn:rel"><sheets>'
                       '<sheet name="统计" r:id="main"/><sheet name="备注" r:id="other"/></sheets></workbook>')
            z.writestr("xl/_rels/workbook.xml.rels", '<Relationships>'
                       '<Relationship Id="main" Target="/xl/worksheets/custom.xml" Type="rel/worksheet"/>'
                       '<Relationship Id="other" Target="worksheets/b.xml" Type="rel/worksheet"/>'
                       '<Relationship Id="str" Target="strings.xml" Type="rel/sharedStrings"/></Relationships>')
            z.writestr("xl/worksheets/custom.xml", '<worksheet xmlns="urn:xl"><sheetData><row>'
                       '<c r="A1" t="s"><v>1</v></c><c r="B1" t="inlineStr"><is><t>日期</t></is></c>'
                       '<c r="D1"><f>SUM(E1:G1)</f><v>27</v></c></row></sheetData></worksheet>')
            z.writestr("xl/strings.xml", '<sst xmlns="urn:xl"><si><t>未用</t></si>'
                       '<si><r><t>复</t></r><r><t>核</t></r></si></sst>')
        self.assertEqual(self.read("账本.xlsx")["sections"], ["统计", "备注"])
        page = self.read("账本.xlsx", "统计", limit=2)
        self.assertEqual(page["items"], [{"cell": "A1", "value": "复核"}, {"cell": "B1", "value": "日期"}])
        self.assertEqual(page["next_offset"], 2)
        page = self.read("账本.xlsx", "统计", offset=2)
        self.assertEqual(page["items"], [{"cell": "D1", "value": "27", "formula": "SUM(E1:G1)"}])
        self.assertIsNone(page["next_offset"])

    def test_paragraphs_and_slide_relationship_order(self):
        with ZipFile(self.work / "材料.docx", "w") as z:
            z.writestr("word/document.xml", '<doc xmlns="urn:w"><p><r><t>上</t></r><r><t>文</t></r></p>'
                       '<tbl><p><t>表格</t></p></tbl></doc>')
        self.assertEqual(self.read("材料.docx", "document")["items"], [{"text": "上文"}, {"text": "表格"}])
        with ZipFile(self.work / "材料.pptx", "w") as z:
            z.writestr("ppt/presentation.xml", '<p xmlns="urn:p" xmlns:r="urn:rel"><sldIdLst>'
                       '<sldId r:id="b"/><sldId r:id="a"/></sldIdLst></p>')
            z.writestr("ppt/_rels/presentation.xml.rels", '<Relationships><Relationship Id="a" Target="slides/slide1.xml"/>'
                       '<Relationship Id="b" Target="slides/slide2.xml"/></Relationships>')
            z.writestr("ppt/slides/slide2.xml", '<slide xmlns="urn:a"><p><t>开场</t></p></slide>')
        self.assertEqual(self.read("材料.pptx")["sections"], ["1", "2"])
        self.assertEqual(self.read("材料.pptx", "1")["items"], [{"text": "开场"}])

    def test_workspace_limits_and_malformed_package(self):
        outside = self.work.parent / "outside.xlsx"
        outside.touch()
        for name in (str(outside), "../outside.xlsx"):
            with self.assertRaisesRegex(ValueError, "工作目录"):
                self.read(name)
        bad = self.work / "bad.xlsx"
        bad.write_bytes(b'incomplete')
        with self.assertRaisesRegex(ValueError, "Office"):
            self.read(bad.name)
