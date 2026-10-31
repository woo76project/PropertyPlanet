from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from app.import_excel import AdaptiveStrings, DiskStrings, open_import_workbook, shared_strings
from app.import_worker import ImportFileError, iter_import_rows, stream_import
from test_property_files import VALID_ROW, spreadsheet

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def shared_workbook(rows=None):
    """Small Microsoft-style fixture, independent of the sample-generation helper."""
    source = BytesIO(spreadsheet([VALID_ROW] if rows is None else rows))
    output = BytesIO()
    with ZipFile(source) as original, ZipFile(output, "w") as result:
        for name in original.namelist():
            data = original.read(name)
            if name == "xl/worksheets/sheet1.xml":
                root = ET.fromstring(data)
                strings = []
                for cell in root.iter(f"{{{NS}}}c"):
                    if cell.get("t") == "inlineStr":
                        inline = cell.find(f"{{{NS}}}is")
                        strings.append(inline if inline is not None else ET.Element(f"{{{NS}}}si"))
                        if inline is not None:
                            cell.remove(inline)
                        strings[-1].tag = f"{{{NS}}}si"
                        cell.set("t", "s")
                        ET.SubElement(cell, f"{{{NS}}}v").text = str(len(strings) - 1)
                data = ET.tostring(root)
            elif name == "[Content_Types].xml":
                root = ET.fromstring(data)
                ET.SubElement(root, "{http://schemas.openxmlformats.org/package/2006/content-types}Override",
                              PartName="/xl/sharedStrings.xml",
                              ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml")
                data = ET.tostring(root)
            result.writestr(name, data)
        root = ET.Element(f"{{{NS}}}sst")
        root.extend(strings)
        result.writestr("xl/sharedStrings.xml", ET.tostring(root))
    return output.getvalue()


class StreamingExcelTests(unittest.TestCase):
    def test_text_parser_preserves_split_utf8_and_ignores_phonetic_annotations(self):
        # Deliberately split tags, UTF-8 codepoints and entity references across reads.
        class SmallReads(BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 7))

        xml = f'<sst xmlns="{NS}"><si><r><t xml:space="preserve"> Ali </t></r><r><t>你好 &amp; Tan</t></r><rPh sb="0" eb="1"><t>annotation</t></rPh></si><si/><si><t>00123</t></si></sst>'
        self.assertEqual(list(shared_strings(SmallReads(xml.encode()))), [" Ali 你好 & Tan", "", "00123"])

    def test_text_parser_rejects_malformed_xml_and_external_doctype(self):
        from defusedxml.common import DefusedXmlException
        with self.assertRaises(ValueError):
            list(shared_strings(BytesIO(f'<sst xmlns="{NS}"><si><t>truncated'.encode())))
        with self.assertRaises(DefusedXmlException):
            list(shared_strings(BytesIO(b'<!DOCTYPE s SYSTEM "file:///unread"><sst/>')))

    def test_small_shared_string_table_stays_in_memory_without_disk_index(self):
        xml = f'<sst xmlns="{NS}"><si><t>Ali</t></si><si><t>00123</t></si></sst>'
        with TemporaryDirectory() as folder, patch("app.import_excel.DiskStrings") as disk:
            strings = AdaptiveStrings(Path(folder) / "strings")
            try:
                strings.load(BytesIO(xml.encode()))
                self.assertEqual([strings[0], strings[1]], ["Ali", "00123"])
                disk.assert_not_called()
                with self.assertRaises(IndexError):
                    strings[-1]
            finally:
                strings.close()

    def test_adaptive_spill_preserves_prefix_empty_and_unicode_values(self):
        values = ["00123", "", "你好", "last"]
        xml = f'<sst xmlns="{NS}">' + ''.join(f'<si><t>{value}</t></si>' for value in values) + '</sst>'
        with TemporaryDirectory() as folder:
            strings = AdaptiveStrings(Path(folder) / "strings", memory_budget=150)
            try:
                strings.load(BytesIO(xml.encode()))
                self.assertIsNotNone(strings.disk)
                self.assertEqual(strings.values, [])
                self.assertEqual([strings[index] for index in range(len(values))], values)
                with self.assertRaises(IndexError):
                    strings[len(values)]
            finally:
                strings.close()

    def test_empty_and_all_empty_disk_string_tables(self):
        for count in (0, 3):
            with self.subTest(count=count), TemporaryDirectory() as folder:
                strings = DiskStrings(Path(folder) / "strings")
                try:
                    xml = f'<sst xmlns="{NS}">' + '<si><t></t></si>' * count + '</sst>'
                    strings.load(BytesIO(xml.encode()))
                    self.assertEqual([strings[index] for index in range(count)], [""] * count)
                    with self.assertRaises(IndexError):
                        strings[count]
                finally:
                    strings.close()

    def test_workbook_disk_fallback_cleans_files_on_early_iterator_close(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "input.xlsx"
            path.write_bytes(shared_workbook())
            factory = lambda path: AdaptiveStrings(path, memory_budget=1)
            with patch("app.import_excel.AdaptiveStrings", side_effect=factory):
                rows = iter_import_rows(path)
                next(rows)
                self.assertTrue(list(Path(folder).glob("xlsx-strings-*")))
                rows.close()
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_shared_and_inline_workbooks_preserve_values_and_cleanup_indexes(self):
        for kind, contents in (("shared", shared_workbook()), ("inline", spreadsheet([VALID_ROW]))):
            with self.subTest(kind=kind), TemporaryDirectory() as folder:
                path = Path(folder) / "input.xlsx"
                path.write_bytes(contents)
                rows = list(iter_import_rows(path))
                self.assertEqual(rows[0][0], 2)
                self.assertEqual(rows[0][1].phone_01, "0123456789")
                self.assertEqual(rows[0][1].ic_number, "001234567890")
                self.assertEqual(rows[0][2], [])
                self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_strings_support_rich_text_escapes_unicode_and_bounded_cache(self):
        xml = f'<sst xmlns="{NS}"><si><t>_x005F_name &amp; 你好</t></si><si><r><t>Ali</t></r><r><t> Tan</t></r></si></sst>'
        with TemporaryDirectory() as folder:
            table = DiskStrings(Path(folder) / "strings.sqlite")
            try:
                table.load(BytesIO(xml.encode()))
                self.assertEqual(table[0], "_name & 你好")
                self.assertEqual(table[1], "Ali Tan")
                self.assertEqual(table.page.cache_info().maxsize, 8)
                for index in (-1, 2):
                    with self.assertRaises(IndexError):
                        table[index]
            finally:
                table.close()

    def test_missing_dimensions_do_not_trigger_full_sheet_scan(self):
        contents = spreadsheet([VALID_ROW])
        output = BytesIO()
        with ZipFile(BytesIO(contents)) as source, ZipFile(output, "w") as target:
            for name in source.namelist():
                data = source.read(name)
                if name == "xl/worksheets/sheet1.xml":
                    root = ET.fromstring(data)
                    root.remove(root.find(f"{{{NS}}}dimension"))
                    data = ET.tostring(root)
                target.writestr(name, data)
        with TemporaryDirectory() as folder:
            path = Path(folder) / "input.xlsx"
            path.write_bytes(output.getvalue())
            with patch("openpyxl.worksheet._reader.WorkSheetParser.parse_dimensions", side_effect=AssertionError("Full scan")):
                self.assertEqual(len(list(iter_import_rows(path))), 1)

    def test_numeric_identifiers_and_formulas_still_report_row_errors(self):
        row = list(VALID_ROW)
        row[1] = 123
        row[5] = "=1+1"
        with TemporaryDirectory() as folder:
            path = Path(folder) / "input.xlsx"
            path.write_bytes(spreadsheet([row]))
            result = list(iter_import_rows(path))
        self.assertEqual({error["field"] for error in result[0][2]}, {"phone_01", "remark"})

    def test_disk_lookup_remains_correct_after_page_eviction(self):
        xml = f'<sst xmlns="{NS}">' + ''.join(f'<si><t>000{i}</t></si>' for i in range(3000)) + '</sst>'
        with TemporaryDirectory() as folder:
            table = DiskStrings(Path(folder) / "strings.sqlite")
            try:
                table.load(BytesIO(xml.encode()))
                for index in (*range(0, 3000, 256), 1, 2999, 256):
                    self.assertEqual(table[index], f"000{index}")
                self.assertLessEqual(table.page.cache_info().currsize, 8)
            finally:
                table.close()

    def test_over_limit_stops_new_batches_and_cleans_index(self):
        with TemporaryDirectory() as folder:
            source = Path(folder) / "input.xlsx"
            source.write_bytes(spreadsheet([VALID_ROW, VALID_ROW]))
            with patch("app.import_worker.MAX_IMPORT_ROWS", 1), patch("app.import_worker.BATCH_ROWS", 1), patch("app.import_worker.process_batch") as batches, patch("app.import_worker.report_total_estimate"), patch("app.import_worker.import_connection") as database:
                with self.assertRaises(ImportFileError):
                    stream_import({"id": "job", "last_row": 1}, source)
            batches.assert_called_once()
            database.assert_not_called()
            self.assertFalse(list(Path(folder).glob("xlsx-strings-*")))

    def test_stream_reads_excel_once_and_saves_valid_and_invalid_rows_together(self):
        @contextmanager
        def connect():
            yield MagicMock()

        bad = list(VALID_ROW)
        bad[0] = ""
        with TemporaryDirectory() as folder:
            source = Path(folder) / "input.xlsx"
            source.write_bytes(spreadsheet([VALID_ROW, bad, VALID_ROW]))
            with patch("app.import_worker.import_connection", connect), patch("app.import_worker.lock_job", return_value={"processed_rows": 0}), patch(
                "app.import_worker.open_import_workbook", wraps=open_import_workbook
            ) as opened, patch("app.import_worker.process_batch") as batches:
                stream_import({"id": "job", "last_row": 1}, source)
                rows = batches.call_args.args[1]
            opened.assert_called_once()
        self.assertEqual([row[0] for row in rows], [2, 3, 4])
        self.assertEqual(rows[0][1].phone_01, "0123456789")
        self.assertIsNone(rows[1][1])
        self.assertTrue(rows[1][2])

    def test_excel_progress_hint_uses_dimensions_without_counting_blank_rows(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "input.xlsx"
            path.write_bytes(spreadsheet([VALID_ROW, [None] * 6, [None] * 6]))
            estimates = []
            rows = iter_import_rows(path, on_total=estimates.append)
            try:
                self.assertEqual(next(rows)[0], 2)
                self.assertEqual(estimates, [3])
                self.assertEqual(list(rows), [])
            finally:
                rows.close()

    def test_progress_estimate_does_not_go_below_committed_count(self):
        from app.import_worker import report_total_estimate
        connection = MagicMock()

        @contextmanager
        def connect():
            yield connection

        with patch("app.import_worker.import_connection", connect), patch(
            "app.import_worker.lock_job", return_value={"processed_rows": 5000}
        ):
            report_total_estimate({"id": "job"}, 4000)
        self.assertEqual(connection.execute.call_args.args[1], (5000, "job"))

    def test_first_batch_is_committed_before_reading_the_remainder(self):
        consumed = []

        def rows(*args, **kwargs):
            for number in (2, 3, 4):
                consumed.append(number)
                yield number, None, [{"field": "owner_name", "message": "Required"}]
            raise ImportFileError("Unreadable trailing data")

        def commit(job, batch):
            self.assertEqual(consumed, [2, 3])

        with patch("app.import_worker.iter_import_rows", side_effect=rows), patch(
            "app.import_worker.BATCH_ROWS", 2
        ), patch("app.import_worker.process_batch", side_effect=commit) as batches:
            with self.assertRaisesRegex(ImportFileError, "Unreadable trailing"):
                stream_import({"id": "job", "last_row": 1}, Path("unused.csv"))
        batches.assert_called_once()

    def test_resume_skips_committed_rows_and_sets_total_only_at_end(self):
        @contextmanager
        def connect():
            yield connection

        connection = MagicMock()
        rows = [(number, None, []) for number in (2, 3, 4)]
        with patch("app.import_worker.iter_import_rows", return_value=(row for row in rows)), patch(
            "app.import_worker.import_connection", connect
        ), patch("app.import_worker.lock_job"), patch("app.import_worker.process_batch") as batches:
            stream_import({"id": "job", "last_row": 3}, Path("unused.csv"))
        self.assertEqual(batches.call_args.args[1], [(4, None, [])])
        self.assertEqual(connection.execute.call_args.args[1], (3, "job"))

    def test_string_xml_entities_are_rejected(self):
        from defusedxml.common import DefusedXmlException
        with TemporaryDirectory() as folder:
            table = DiskStrings(Path(folder) / "strings.sqlite")
            try:
                with self.assertRaises(DefusedXmlException):
                    table.load(BytesIO(b'<!DOCTYPE s [<!ENTITY x "bad">]><sst><si><t>&x;</t></si></sst>'))
            finally:
                table.close()
