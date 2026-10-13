from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import cache


class SessionCacheTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self._original_path = cache._DB_PATH
        cache._DB_PATH = Path(self._temp.name) / "test_cache.db"

    def tearDown(self):
        cache._DB_PATH = self._original_path
        self._temp.cleanup()

    def test_empty_session_snapshot_is_still_a_valid_cache(self):
        cache.save_sessions([])

        sessions, age = cache.load_sessions()

        self.assertEqual(sessions, [])
        self.assertIsNotNone(age)

    def test_empty_setup_counts_are_distinct_from_missing_snapshot(self):
        counts, age = cache.load_setup_counts()
        self.assertIsNone(counts)
        self.assertIsNone(age)

        cache.save_setup_counts({})
        counts, age = cache.load_setup_counts()

        self.assertEqual(counts, {})
        self.assertIsNotNone(age)

    def test_setup_identity_summaries_round_trip(self):
        expected = {
            "race-69": ["Carson Bowers", "#24/42 Redspeed", "#11 2021 Birel"]
        }

        cache.save_setup_summaries(expected)

        self.assertEqual(cache.load_setup_summaries(), expected)


if __name__ == "__main__":
    unittest.main()
