import unittest

from pydantic import ValidationError

from app.auth import ProfileUpdate


class ProfilePhoneTests(unittest.TestCase):
    def profile(self, phone):
        return ProfileUpdate(username="test_user", first_name="Test", last_name="User", phone=phone)

    def test_international_numbers_are_stored_in_one_format(self):
        for raw, expected in (
            (" +60 12-345 6789 ", "+60123456789"),
            ("+65 8123 4567", "+6581234567"),
            ("+44 20 8366 1177", "+442083661177"),
            ("+1 (213) 373-4253", "+12133734253"),
        ):
            with self.subTest(phone=raw):
                self.assertEqual(self.profile(raw).phone, expected)

    def test_local_ambiguous_incomplete_and_extension_inputs_are_rejected(self):
        for phone in ("0123456789", "60123456789", "+60", "+60123", "+99912345678",
                      "+60123456789 ext 12", "+60 hello", "", "+000000000"):
            with self.subTest(phone=phone), self.assertRaises(ValidationError):
                self.profile(phone)
