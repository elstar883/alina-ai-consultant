import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from alina_bot.gigachat_client import _extract_content, _parse_expiry  # noqa: E402


class ParseExpiryTest(unittest.TestCase):
    def test_expires_at_in_seconds(self) -> None:
        self.assertAlmostEqual(_parse_expiry({"expires_at": 1_800_000_000}), 1_800_000_000)

    def test_expires_at_in_milliseconds(self) -> None:
        self.assertAlmostEqual(
            _parse_expiry({"expires_at": 1_800_000_000_000}), 1_800_000_000
        )

    def test_expires_in_is_relative_to_now(self) -> None:
        before = time.time()
        expires_at = _parse_expiry({"expires_in": 1800})
        after = time.time()
        self.assertGreaterEqual(expires_at, before + 1800)
        self.assertLessEqual(expires_at, after + 1800)

    def test_fallback_is_25_minutes(self) -> None:
        before = time.time()
        expires_at = _parse_expiry({})
        self.assertGreaterEqual(expires_at, before + 25 * 60)


class ExtractContentTest(unittest.TestCase):
    def test_plain_string(self) -> None:
        self.assertEqual(_extract_content("привет"), "привет")

    def test_list_of_parts(self) -> None:
        content = [{"text": "первая"}, "вторая", {"other": 1}]
        self.assertEqual(_extract_content(content), "первая\nвторая")

    def test_unknown_type(self) -> None:
        self.assertEqual(_extract_content(None), "")


if __name__ == "__main__":
    unittest.main()
