import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.auth import require_complete_profile
from app.main import app


class PropertySummaryTests(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_total_requires_admin_before_counting(self):
        with patch("app.properties.psycopg.connect") as connection:
            self.assertEqual(TestClient(app).get("/properties/summary").status_code, 401)
            app.dependency_overrides[require_complete_profile] = lambda: {"id": 2, "role": "User"}
            self.assertEqual(TestClient(app).get("/properties/summary").status_code, 403)
            connection.assert_not_called()

    def test_total_counts_all_records_independent_of_filters_and_pagination(self):
        app.dependency_overrides[require_complete_profile] = lambda: {"id": 1, "role": "Admin"}
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = {"total": 500123}
        with patch("app.properties.psycopg.connect") as connect:
            connect.return_value.__enter__.return_value = connection
            response = TestClient(app).get("/properties/summary?search=Ali&before_id=4&limit=20")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"total": 500123})
        self.assertEqual(connection.execute.call_args.args, ("SELECT COUNT(*) AS total FROM properties",))
