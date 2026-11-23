"""Tests for the DuckDB parquet reader that serves the site."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import lake


@unittest.skipUnless(lake.available(), "bundled parquet not present")
class TestLake(unittest.TestCase):
    def test_stats_are_real(self):
        s = lake.stats()
        self.assertGreater(s["n"], 1_000_000)
        self.assertEqual(s["dmax"], "2026-09-28")
        self.assertGreater(s["m"], 1000)

    def test_search_returns_rows(self):
        rows = lake.search(commodity="Potato", limit=5)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(r["commodity"] == "Potato" for r in rows))
        self.assertIsNotNone(rows[0]["modal_price"])

    def test_search_is_case_insensitive(self):
        a = lake.search(q="potato", limit=5)
        b = lake.search(q="POTATO", limit=5)
        self.assertEqual(len(a), 5)
        self.assertEqual(len(b), 5)

    def test_trend_has_movement(self):
        t = lake.commodity_trend("Potato")
        self.assertGreater(len(t), 100)
        self.assertGreater(len({p["avg_price"] for p in t}), 10)

    def test_summary_has_week_on_week(self):
        """The risers/fallers strip needs a pct on every crop."""
        s = lake.summary(limit=40)
        self.assertGreater(len(s), 5)
        self.assertTrue(any(x["pct"] is not None for x in s),
                        "no crop has a week-on-week change — the prev-period "
                        "join in _summary is broken")

    def test_market_spread(self):
        sp = lake.market_spread("Onion", limit=10)
        self.assertGreater(len(sp), 0)
        self.assertIn("pct", sp[0])

    def test_summary_by_state(self):
        s = lake.summary("Karnataka", limit=10)
        self.assertGreater(len(s), 0)

    def test_unknown_commodity_is_empty_not_an_error(self):
        self.assertEqual(lake.commodity_trend("Nonexistent Crop"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
