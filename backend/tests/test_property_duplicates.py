import unittest
from types import SimpleNamespace
from unittest.mock import patch

import psycopg
from fastapi import HTTPException
from pydantic import SecretStr

from app.properties import PropertyCreate, PropertyUpdate, create_property, update_property
from app.property_files import save_properties


def record(**changes):
    return PropertyCreate.model_validate({
        "owner_name": "Ali", "phone_01": "0123",
        **changes,
    })


class PropertyDuplicateTests(unittest.TestCase):
    def test_file_and_database_duplicates_counted_separately(self):
        # Of A, A, B, only B is new in the database.
        with patch("app.property_files.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.property_files.psycopg.connect") as connect:
            connection = connect.return_value.__enter__.return_value
            cursor = connection.cursor.return_value.__enter__.return_value
            cursor.rowcount = 1
            result = save_properties([record(), record(owner_name=" Ali "), record(owner_name="Bob")], 1)
            self.assertEqual(result, {"imported": 1, "duplicates_skipped": 2,
                                      "duplicates_in_file": 1, "already_in_database": 1})
            self.assertEqual(len(cursor.executemany.call_args.args[1]), 2)
            self.assertEqual(connection.execute.call_args.args[1][4].obj, {"count": 1, "duplicates_skipped": 2})

    def test_all_existing_rows_are_skipped(self):
        with patch("app.property_files.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.property_files.psycopg.connect") as connect:
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.rowcount = 0
            self.assertEqual(save_properties([record(), record()], 1), {
                "imported": 0, "duplicates_skipped": 2, "duplicates_in_file": 1, "already_in_database": 1,
            })

    def test_case_and_any_field_difference_remain_separate(self):
        records = [record(), record(owner_name="ali"), record(owner_name="Bob"),
                   record(phone_01="0456"), record(phone_02="0789"), record(ic_number="001"),
                   record(address="KL"), record(remark="Call later"), record(address=None),
                   record().model_copy(update={"address": " "})]
        with patch("app.property_files.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.property_files.psycopg.connect") as connect:
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cursor.rowcount = 8
            result = save_properties(records, 1)
            self.assertEqual(result["imported"], 8)
            self.assertEqual(result["duplicates_in_file"], 2)
            self.assertEqual(len(cursor.executemany.call_args.args[1]), 8)

    def test_insert_batches_share_one_transaction(self):
        records = [record(owner_name=f"Owner {index}") for index in range(501)]
        with patch("app.property_files.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.property_files.psycopg.connect") as connect:
            cursor = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            def insert(query, rows):
                cursor.rowcount = len(rows)
            cursor.executemany.side_effect = insert
            result = save_properties(records, 1)
            self.assertEqual(result["imported"], 501)
            self.assertEqual([len(call.args[1]) for call in cursor.executemany.call_args_list], [500, 1])
            connect.assert_called_once()

    def test_single_create_and_edit_return_conflict_without_success_log(self):
        for operation in (lambda: create_property(record(), {"id": 1}),
                          lambda: update_property(PropertyUpdate(owner_name="Ali"), 9, {"id": 1})):
            with patch("app.properties.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.properties.psycopg.connect") as connect, patch("app.properties.record_audit") as audit:
                connect.return_value.__enter__.return_value.execute.side_effect = psycopg.errors.UniqueViolation("duplicate")
                with self.assertRaises(HTTPException) as caught:
                    operation()
                self.assertEqual(caught.exception.status_code, 409)
                audit.assert_not_called()
                self.assertIsNotNone(connect.return_value.__exit__.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
