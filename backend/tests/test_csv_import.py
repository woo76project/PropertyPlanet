import csv
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth import require_complete_profile
from app.main import app
from app.property_files import LABELS, parse_csv_properties, parse_properties
from test_property_files import VALID_ROW, spreadsheet


def csv_file(rows, headers=LABELS, delimiter=",", bom=False):
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=delimiter)
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig" if bom else "utf-8")


class CsvImportTests(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def authorize_admin(self):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 1, "role": "Admin"}

    def test_csv_and_excel_produce_same_cleaned_records(self):
        row = list(VALID_ROW)
        row[0] = "  Sunrise Condo  "
        row[0] = "李 Ali"
        expected = parse_properties(spreadsheet([row]))[0].model_dump()
        for delimiter in (",", ";", "\t"):
            for bom in (True, False):
                with self.subTest(delimiter=delimiter, bom=bom):
                    records = parse_csv_properties(csv_file([row, [""] * 6], delimiter=delimiter, bom=bom))
                    self.assertEqual(len(records), 1)
                    self.assertEqual(records[0].model_dump(), expected)
                    self.assertEqual(records[0].phone_01, "0123456789")
                    self.assertEqual(records[0].ic_number, "001234567890")

    def test_quotes_separators_and_newlines_are_preserved(self):
        row = list(VALID_ROW)
        row[0] = 'Condo "A", Block B'
        row[4] = "Lot 1, Jalan A\nKuala Lumpur"
        parsed = parse_csv_properties(csv_file([row]))[0]
        self.assertEqual(parsed.owner_name, row[0])
        self.assertEqual(parsed.address, row[4])

    def test_minimum_columns_and_blank_optional_fields(self):
        record = parse_csv_properties(csv_file(
            [["Ali", "0123"]],
            headers=["Name", "Phone 01"],
        ))[0]
        self.assertIsNone(record.phone_02)
        self.assertIsNone(record.address)
        self.assertIsNone(record.remark)

    def test_csv_upload_skips_duplicates_and_uses_existing_sql(self):
        self.authorize_admin()
        for inserted in (0, 1):
            with self.subTest(inserted=inserted), patch("app.property_files.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.property_files.psycopg.connect") as connect:
                cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
                cursor.rowcount = inserted
                response = TestClient(app).post("/properties/import", files={
                    "file": ("properties.CSV", csv_file([VALID_ROW, VALID_ROW], bom=True), "text/csv"),
                })
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.json(), {
                    "imported": inserted, "duplicates_skipped": 2 - inserted,
                    "duplicates_in_file": 1, "already_in_database": 1 - inserted,
                })
                query, values = cursor.executemany.call_args.args
                self.assertIn("ON CONFLICT ON CONSTRAINT properties_duplicate_key_unique DO NOTHING", query)
                self.assertEqual(values, [tuple(VALID_ROW) + (1,)])

    def test_csv_upload_still_requires_admin(self):
        with patch("app.property_files.psycopg.connect") as connect:
            client = TestClient(app)
            self.assertEqual(client.post("/properties/import", files={"file": ("data.csv", csv_file([VALID_ROW]))}).status_code, 401)
            app.dependency_overrides[require_complete_profile] = lambda: {"id": 2, "role": "User"}
            self.assertEqual(client.post("/properties/import", files={"file": ("data.csv", csv_file([VALID_ROW]))}).status_code, 403)
            connect.assert_not_called()

    def test_invalid_record_blocks_the_entire_csv(self):
        self.authorize_admin()
        row = list(VALID_ROW)
        row[0] = ""
        with patch("app.property_files.psycopg.connect") as connect:
            response = TestClient(app).post("/properties/import", files={"file": ("data.csv", csv_file([VALID_ROW, row]))})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["detail"]["rows"][0]["row"], 3)
            connect.assert_not_called()

    def test_missing_extra_or_malformed_columns_are_rejected(self):
        invalid_files = (
            csv_file([VALID_ROW], headers=["Wrong"] * 6),
            csv_file([VALID_ROW[:-1]]),
            csv_file([VALID_ROW + ["extra"]]),
            csv_file([VALID_ROW], headers=["Name"] * 6),
            csv_file([]) + b'"unclosed quote',
        )
        for content in invalid_files:
            with self.subTest(content=content[:30]):
                with self.assertRaises(HTTPException) as caught:
                    parse_csv_properties(content)
                self.assertEqual(caught.exception.status_code, 422)

    def test_empty_and_non_utf8_csv_have_clear_errors(self):
        for content in (b"", csv_file([]), csv_file([]) + b"\xff"):
            with self.assertRaises(HTTPException) as caught:
                parse_csv_properties(content)
            self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("UTF-8", caught.exception.detail)

    def test_size_and_row_limits_apply_to_csv(self):
        content = csv_file([VALID_ROW, VALID_ROW])
        for variable, value in (("MAX_FILE_BYTES", 1), ("MAX_ROWS", 1)):
            with patch("app.property_files." + variable, value):
                with self.assertRaises(HTTPException) as caught:
                    parse_csv_properties(content)
                self.assertEqual(caught.exception.status_code, 413)

    def test_csv_formula_looking_values_remain_literal_text(self):
        row = list(VALID_ROW)
        row[0] = "=Something"
        self.assertEqual(parse_csv_properties(csv_file([row]))[0].owner_name, "=Something")


if __name__ == "__main__":
    unittest.main()
