from io import BytesIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import psycopg
from fastapi import HTTPException
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from pydantic import SecretStr

from app.auth import require_complete_profile
from app.main import app
from app.property_files import FIELDS, LABELS, excel_response, parse_properties, save_properties


def spreadsheet(rows, headers=LABELS):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


VALID_ROW = ["Ali", "0123456789", None, "001234567890", "KL", "Call after 6pm"]


class PropertyFilesTests(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def authorize_admin(self):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 1, "role": "Admin"}

    def test_file_routes_require_admin_even_for_paid_users(self):
        with patch("app.property_files.psycopg.connect") as connect:
            client = TestClient(app)
            for expected in (401, 403):
                for url in ("/properties/import-template", "/properties/export"):
                    self.assertEqual(client.get(url).status_code, expected)
                response = client.post("/properties/import", files={"file": ("data.xlsx", spreadsheet([VALID_ROW]))})
                self.assertEqual(response.status_code, expected)
                app.dependency_overrides[require_complete_profile] = lambda: {"id": 2, "role": "User"}
            connect.assert_not_called()

    def test_template_has_text_identifiers_and_six_columns(self):
        self.authorize_admin()
        response = TestClient(app).get("/properties/import-template")
        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(BytesIO(response.content))
        try:
            sheet = workbook.active
            self.assertEqual(tuple(cell.value for cell in sheet[1]), ("Name", "Phone 01", "Phone 02", "IC number", "Address", "Remark"))
            self.assertEqual(sheet["C2"].number_format, "@")
            self.assertEqual(sheet["D2"].number_format, "@")
            self.assertEqual(len(sheet.data_validations.dataValidation), 0)
        finally:
            workbook.close()

    def test_import_validates_workbook_and_inserts_with_admin_id(self):
        self.authorize_admin()
        with patch("app.property_files.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.property_files.psycopg.connect") as connect:
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.rowcount = 1
            response = TestClient(app).post("/properties/import", files={"file": ("data.xlsx", spreadsheet([VALID_ROW, [None] * 6]))})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json(), {"imported": 1, "duplicates_skipped": 0,
                                              "duplicates_in_file": 0, "already_in_database": 0})
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            query, records = cursor.executemany.call_args.args
            self.assertIn("VALUES (%s, %s, %s, %s, %s, %s, %s)", query)
            self.assertEqual(records, [tuple(VALID_ROW) + (1,)])
            self.assertIn("ON CONFLICT ON CONSTRAINT properties_duplicate_key_unique DO NOTHING", query)

    def test_new_headers_reordered_optional_remark_and_owner_name_alias(self):
        headers = ["Remark", "Address", "IC number", "Phone 02", "Phone 01", "Name"]
        values = ["First line\nSecond line", "Jalan A", "001234", "0190000", "0110000", "Owner"]
        record = parse_properties(spreadsheet([values], headers=headers))[0]
        self.assertEqual(record.owner_name, "Owner")
        self.assertEqual(record.remark, values[0])
        self.assertEqual(record.phone_01, "0110000")
        self.assertEqual(record.ic_number, "001234")
        minimal = parse_properties(spreadsheet([["Owner", "0110000"]], headers=["owner_name", "Phone 01"]))[0]
        self.assertIsNone(minimal.remark)
        for headings in (["Name", "Owner Name", "Phone 01"], ["Property Name", "Name", "Phone 01"], ["Name", "Phone 01", "Listing Purpose"]):
            with self.assertRaises(HTTPException):
                parse_properties(spreadsheet([], headers=headings))

    def test_remark_text_round_trips_and_length_is_validated(self):
        row = [*VALID_ROW[:-1], "=literal remark\nSecond line"]
        parsed = parse_properties(excel_response([dict(zip(FIELDS, row))], "test.xlsx").body)[0]
        self.assertEqual(parsed.remark, row[-1])
        with self.assertRaises(HTTPException):
            parse_properties(spreadsheet([[*VALID_ROW[:-1], "x" * 2001]]))

    def test_invalid_row_prevents_any_insert(self):
        self.authorize_admin()
        bad = list(VALID_ROW)
        bad[0] = ""
        with patch("app.property_files.psycopg.connect") as connect:
            response = TestClient(app).post("/properties/import", files={"file": ("data.xlsx", spreadsheet([VALID_ROW, bad]))})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"]["rows"][0]["row"], 3)
            connect.assert_not_called()

    def test_numeric_identifiers_and_formulas_are_rejected(self):
        for index, value in ((1, 123456789), (3, 123456), (0, '=HYPERLINK("https://example.com")')):
            with self.subTest(index=index):
                row = list(VALID_ROW)
                row[index] = value
                with self.assertRaises(HTTPException) as caught:
                    parse_properties(spreadsheet([row]))
                self.assertEqual(caught.exception.status_code, 422)
                self.assertEqual(caught.exception.detail["rows"][0]["errors"][0]["field"], FIELDS[index])

    def test_export_round_trip_preserves_text_and_does_not_create_formulas(self):
        row = dict(zip(FIELDS, VALID_ROW))
        row["owner_name"] = "=Something"
        response = excel_response([row], "properties.xlsx")
        workbook = load_workbook(BytesIO(response.body))
        try:
            self.assertEqual(workbook.active["A2"].data_type, "s")
        finally:
            workbook.close()
        parsed = parse_properties(response.body)[0]
        self.assertEqual(parsed.owner_name, "=Something")
        self.assertEqual(parsed.phone_01, "0123456789")
        self.assertEqual(parsed.ic_number, "001234567890")

    def test_invalid_headers_empty_file_and_size_limits(self):
        invalid_cases = (b"not an Excel file", spreadsheet([]), spreadsheet([VALID_ROW], headers=["Wrong"] * 7))
        for content in invalid_cases:
            with self.assertRaises(HTTPException) as caught:
                parse_properties(content)
            self.assertEqual(caught.exception.status_code, 422)
        with patch("app.property_files.MAX_FILE_BYTES", 1):
            with self.assertRaises(HTTPException) as caught:
                parse_properties(b"too big")
            self.assertEqual(caught.exception.status_code, 413)
        with patch("app.property_files.MAX_ROWS", 1):
            with self.assertRaises(HTTPException) as caught:
                parse_properties(spreadsheet([VALID_ROW, VALID_ROW]))
            self.assertEqual(caught.exception.status_code, 413)
        with patch("app.property_files.MAX_UNPACKED_BYTES", 1):
            with self.assertRaises(HTTPException) as caught:
                parse_properties(spreadsheet([VALID_ROW]))
            self.assertEqual(caught.exception.status_code, 413)

    def test_export_applies_filters_and_refuses_truncation(self):
        self.authorize_admin()
        with patch("app.property_files.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.property_files.psycopg.connect") as connect:
            execute = connect.return_value.__enter__.return_value.execute
            execute.return_value.fetchall.return_value = [dict(zip(FIELDS, VALID_ROW))]
            response = TestClient(app).get("/properties/export", params={"search": "ali%"})
            self.assertEqual(response.status_code, 200)
            query, values = execute.call_args_list[0].args
            self.assertIn("remark ILIKE", query)
            self.assertEqual(values, tuple(["%ali!%%"] * 6 + [10001]))
            self.assertEqual(len(parse_properties(response.content)), 1)
            with patch("app.property_files.MAX_ROWS", 0):
                self.assertEqual(TestClient(app).get("/properties/export").status_code, 413)

    def test_db_insert_failure_exits_transaction_with_error(self):
        records = parse_properties(spreadsheet([VALID_ROW]))
        with patch("app.property_files.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.property_files.psycopg.connect") as connect:
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.executemany.side_effect = psycopg.OperationalError("private database details")
            with self.assertRaises(HTTPException) as caught:
                save_properties(records, 1)
            self.assertEqual(caught.exception.status_code, 503)
            self.assertIsNotNone(connect.return_value.__exit__.call_args.args[0])
            self.assertNotIn("private", caught.exception.detail)

    def test_unsupported_extension_is_rejected(self):
        self.authorize_admin()
        with patch("app.property_files.psycopg.connect") as connect:
            response = TestClient(app).post("/properties/import", files={"file": ("data.xls", b"a,b")})
            self.assertEqual(response.status_code, 422)
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
