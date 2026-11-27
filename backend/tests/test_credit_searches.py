from contextlib import ExitStack
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from pydantic import SecretStr
from app.auth import require_complete_profile
from app.main import app
from app.properties import CreditSearchRequest


class CreditSearchTests(unittest.TestCase):
    def setUp(self):
        guard = patch("app.search_rate_limit.check_search_limit")
        guard.start()
        self.addCleanup(guard.stop)
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.addCleanup(app.dependency_overrides.clear)
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 2, "role": "User"}
        self.stack.enter_context(patch("app.properties.get_settings", return_value=SimpleNamespace(db_url=SecretStr("unused"))))
        self.connect = self.stack.enter_context(patch("app.properties.psycopg.connect"))
        self.execute = self.connect.return_value.__enter__.return_value.execute
        self.user = {"id": 2, "role": "User", "status": "Active"}
        self.body = {"request_id": str(uuid4()), "search": "Garden", "limit": 1}
        self.client = TestClient(app)
        self.execute.return_value.fetchall.return_value = [{"id": 20}, {"id": 19}]

    def post(self, **changes):
        return self.client.post("/properties/search", json={**self.body, **changes})

    def new_search(self, balance=2):
        self.execute.return_value.fetchone.side_effect = [self.user, None, {"balance": balance}, {"max_id": 20}, {"balance": balance - 1}]

    def prior(self, **changes):
        data = CreditSearchRequest.model_validate(self.body)
        canonical = data.model_dump(mode="json", exclude={"request_id", "before_id", "limit"})
        return {"user_id": 2, "query_hash": hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest(), "max_property_id": 20, **changes}

    def test_success_deducts_once_and_records_search_scope(self):
        self.new_search()
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["balance"], 1)
        self.assertTrue(response.json()["charged"])
        self.assertEqual(response.json()["pagination"]["next_before_id"], 20)
        calls = self.execute.call_args_list
        self.assertIn("FOR UPDATE", calls[0].args[0])
        self.assertIn("credit_balance = credit_balance - 1", calls[5].args[0])
        self.assertIn("INSERT INTO credit_searches", calls[6].args[0])
        self.assertIn("id <= %s", calls[4].args[0])

    def test_empty_200_never_deducts_or_records_a_paid_search(self):
        self.new_search()
        self.execute.return_value.fetchall.return_value = []
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["charged"])
        self.assertEqual(response.json()["balance"], 2)
        self.assertFalse(any("credit_balance = credit_balance - 1" in call.args[0] or "INSERT INTO credit_searches" in call.args[0]
                             for call in self.execute.call_args_list))

    def test_zero_credits_block_full_records_before_property_query(self):
        self.execute.return_value.fetchone.side_effect = [self.user, None, None]
        self.assertEqual(self.post().status_code, 402)
        self.assertFalse(any("FROM properties" in call.args[0] for call in self.execute.call_args_list))

    def test_existing_search_paging_and_refresh_work_with_zero_balance(self):
        for before_id in (None, 20):
            self.execute.reset_mock()
            self.execute.return_value.fetchone.side_effect = [self.user, self.prior(), {"balance": 0}]
            response = self.post(before_id=before_id)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["charged"])
            self.assertEqual(response.json()["balance"], 0)
            self.assertFalse(any("credit_balance = credit_balance - 1" in call.args[0] for call in self.execute.call_args_list))

    def test_changed_conditions_or_other_users_cannot_reuse_a_search(self):
        for prior, changes in ((self.prior(), {"search": "Other"}), (self.prior(user_id=99), {})):
            self.execute.return_value.fetchone.side_effect = [self.user, prior]
            self.assertEqual(self.post(**changes).status_code, 409)

    def test_failed_query_never_reaches_debit(self):
        self.new_search()
        def run(query, *values):
            if "FROM properties WHERE" in query:
                raise psycopg.OperationalError("private")
            return self.execute.return_value
        self.execute.side_effect = run
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)
        self.assertFalse(any("credit_balance = credit_balance - 1" in call.args[0] for call in self.execute.call_args_list))
        self.assertIsNotNone(self.connect.return_value.__exit__.call_args.args[0])

    def test_suspension_recheck_blocks_even_an_existing_search(self):
        self.execute.return_value.fetchone.return_value = {**self.user, "status": "Suspended"}
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.execute.call_count, 1)

    def test_admin_search_is_free(self):
        self.execute.return_value.fetchone.side_effect = [{**self.user, "role": "Admin"}, {"max_id": 20}]
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["charged"])
        self.assertTrue(response.json()["unlimited"])
        self.assertFalse(any("credit_balance = credit_balance - 1" in call.args[0] for call in self.execute.call_args_list))
        self.assertFalse(any("credit_balance AS balance" in call.args[0] or "FROM credit_searches" in call.args[0] for call in self.execute.call_args_list))

    def test_no_blank_search_and_no_extra_fields(self):
        for changes in ({"search": " "}, {"limit": 101}, {"request_id": "bad"}, {"balance": 100}, {"filters": {"match": "all", "conditions": []}}):
            self.assertEqual(self.post(**changes).status_code, 422)
        self.connect.assert_not_called()

    def test_direct_full_record_routes_are_admin_only(self):
        self.assertEqual(self.client.get("/properties").status_code, 403)
        self.assertEqual(self.client.get("/properties/1").status_code, 403)
        self.connect.assert_not_called()

    def test_six_conditions_per_category_rejected_without_query_or_charge(self):
        for field in ("owner_name", "address"):
            for match in ("all", "any"):
                filters = {"match": match, "conditions": [
                    {"field": field, "operator": "equals", "value": str(i)} for i in range(6)
                ]}
                self.assertEqual(self.post(filters=filters).status_code, 422)
        self.connect.assert_not_called()
