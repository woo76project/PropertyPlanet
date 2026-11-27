import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth import require_complete_profile
from app.auth import require_admin
from app.main import app
from app.property_search import builder_clause, quick_search_clause


def builder(*rows, match="all"):
    return json.dumps({"match": match, "conditions": [
        {"field": field, "operator": operator, "value": value}
        for field, operator, value in rows
    ]})


class PropertySearchTests(unittest.TestCase):
    def setUp(self):
        guard = patch("app.search_rate_limit.check_search_limit")
        guard.start()
        self.addCleanup(guard.stop)

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_literals_operators_and_parameterized_values(self):
        clause, values = builder_clause(builder(
            ("address", "contains", "  ABC%_!  "),
            ("owner_name", "equals", "Tan"),
            ("address", "starts_with", "J_1"),
            ("address", "ends_with", "Lumpur"),
        ))
        self.assertEqual(values, ["%ABC!%!_!!%", "Tan", "Tan", "J!_1%", "J!_1%", "%Lumpur"])
        self.assertIn("LOWER(owner_name) = LOWER(%s)", clause)
        self.assertIn("LOWER(address) LIKE LOWER(%s) ESCAPE '!'", clause)
        self.assertNotIn("Lumpur", clause)
        self.assertEqual(clause.count(" AND "), 5)
        self.assertIn("owner_name ILIKE %s ESCAPE '!'", clause)

    def test_invalid_conditions_never_become_sql(self):
        cases = ["not json", "[]", "{}", builder(),
                 builder(("owner_name; DROP TABLE users", "equals", "ABC")),
                 builder(("address", "not_contains", "ABC")),
                 builder(("address", "equals", "   ")),
                 builder(*([("address", "equals", "KL")] * 7))]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(HTTPException) as error:
                builder_clause(raw)
            self.assertEqual(error.exception.status_code, 422)

    def test_all_operators_accept_one_character_and_reject_blank_clues(self):
        for operator in ("equals", "starts_with", "contains", "ends_with"):
            for value in ("w", "Ng", " 吉 ", "%", "_", "a b c"):
                with self.subTest(operator=operator, value=value):
                    self.assertTrue(builder_clause(builder(("owner_name", operator, value)))[0])
            for value in ("", " ", "  "):
                with self.subTest(operator=operator, value=value), self.assertRaises(HTTPException):
                    builder_clause(builder(("owner_name", operator, value)))
        self.assertEqual(quick_search_clause("w", preview=True)[1], ["%w%"])
        self.assertEqual(quick_search_clause("%", preview=True)[1], ["%!%%"])

    def test_quick_search_restores_six_literal_or_branches(self):
        clause, values = quick_search_clause(" ABC%_! ")
        self.assertNotIn("COALESCE", clause)
        self.assertEqual(clause.count(" OR "), 5)
        self.assertEqual(values, ["%ABC!%!_!!%"] * 6)
        self.assertEqual(quick_search_clause("ABC", preview=True), ("address ILIKE %s ESCAPE '!'", ["%ABC%"]))

    def test_blank_normal_search_rejected_on_all_routes_before_database_or_debit(self):
        app.dependency_overrides[require_admin] = lambda: {"id": 1, "role": "Admin"}
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 2, "role": "User"}
        from uuid import uuid4
        with patch("app.properties.psycopg.connect") as connect:
            client = TestClient(app)
            for value in ("", " ", "  "):
                for path in ("/properties", "/properties/preview"):
                    self.assertEqual(client.get(path, params={"search": value}).status_code, 422)
                self.assertEqual(client.post("/properties/search", json={"request_id": str(uuid4()), "search": value}).status_code, 422)
            connect.assert_not_called()

    def test_free_preview_accepts_name_and_address_without_revealing_private_fields(self):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 2, "role": "User"}
        with patch("app.properties.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.properties.psycopg.connect") as connect:
            execute = connect.return_value.__enter__.return_value.execute
            execute.return_value.fetchall.return_value = [{
                "id": 9, "owner_name": "ABC", "address": "Address A",
                "phone_01": "private", "phone_02": "private", "ic_number": "private", "remark": "private",
            }]
            for match in ("all", "any"):
                response = TestClient(app).get("/properties/preview", params={"filters": builder(
                    ("owner_name", "contains", "A"), ("address", "contains", "B"), match=match)})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["properties"], [{"id": 9, "owner_name": "ABC", "address": "Address A"}])
                sql, values = execute.call_args.args
                self.assertIn("owner_name ILIKE %s", sql)
                self.assertIn("address ILIKE %s", sql)
                self.assertEqual(values, ("%A%", "%B%", 21))
                self.assertNotIn("private", response.text)
                self.assertNotIn("credit_balance", sql)
                for field in ("phone_01", "phone_02", "ic_number", "remark"):
                    self.assertNotIn(field, sql)

    def test_five_conditions_per_category_allowed_six_rejected_for_and_and_or(self):
        for field in ("address", "owner_name", "address"):
            for match in ("all", "any"):
                with self.subTest(field=field, match=match):
                    rows = [(field, "equals", f"{index:03}") for index in range(5)]
                    self.assertEqual(len(builder_clause(builder(*rows, match=match))[1]), 10)
                    with self.assertRaises(HTTPException) as error:
                        builder_clause(builder(*rows, (field, "equals", "sixth"), match=match))
                    self.assertEqual(error.exception.status_code, 422)

    def test_mixed_categories_allow_ten_conditions_and_reject_eleven(self):
        rows = [("address", "equals", f"{i:03}") for i in range(5)]
        rows += [("owner_name", "equals", f"{i:03}") for i in range(5)]
        self.assertEqual(len(builder_clause(builder(*rows))[1]), 20)
        with self.assertRaises(HTTPException) as error:
            builder_clause(builder(*rows, ("address", "equals", "KL")))
        self.assertEqual(error.exception.status_code, 422)

    def test_excess_category_conditions_rejected_before_preview_database_query(self):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 2, "role": "User"}
        with patch("app.properties.psycopg.connect") as connect:
            response = TestClient(app).get("/properties/preview", params={"filters": builder(
                *[("address", "equals", f"{i:03}") for i in range(6)])})
            self.assertEqual(response.status_code, 422)
            connect.assert_not_called()

    def test_any_group_stays_inside_cursor_constraints(self):
        for endpoint, dependency in (("/properties", require_admin),
                                     ("/properties/preview", require_complete_profile)):
            app.dependency_overrides[dependency] = lambda: {"id": 2, "role": "User"}
            with patch("app.properties.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))), patch("app.properties.psycopg.connect") as connect:
                execute = connect.return_value.__enter__.return_value.execute
                execute.return_value.fetchall.return_value = []
                response = TestClient(app).get(endpoint, params={"filters": builder(
                    ("address", "contains", "Damansara"), ("address", "contains", "Jalan"), match="any"),
                    "before_id": 99, "limit": 20})
                self.assertEqual(response.status_code, 200)
                query, values = execute.call_args.args
                self.assertIn("(address ILIKE %s ESCAPE '!' OR address ILIKE %s ESCAPE '!') AND id", query)
                self.assertEqual(values, ("%Damansara%", "%Jalan%", 99, 21))
                self.assertNotIn("COUNT(", query)
                self.assertNotIn("OFFSET", query)
            app.dependency_overrides.clear()

    def test_anonymous_and_free_users_cannot_use_paid_search(self):
        client = TestClient(app)
        self.assertEqual(client.get("/properties", params={"filters": builder(("owner_name", "equals", "Ng"))}).status_code, 401)
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 2, "role": "User"}
        with patch("app.properties.psycopg.connect") as connect:
            connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None
            self.assertEqual(client.get("/properties", params={"filters": builder(("owner_name", "equals", "Ng"))}).status_code, 403)
