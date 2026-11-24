import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MANDI_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")

from app import config, db          # noqa: E402
config.DB_PATH = os.environ["MANDI_DB"]
from app.main import app            # noqa: E402


class TestWeb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()
        from ingest.fetch import run
        run("fixture", verbose=False)
        cls.c = app.test_client()

    def test_health_is_instant(self):
        """
        Render treats a slow /health as a dead service and restarts it, so this
        endpoint must never touch the database or the disk.
        """
        t0 = time.time()
        r = self.c.get("/health")
        elapsed = time.time() - t0
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertIn("records", r.get_json())
        self.assertLess(elapsed, 0.5)

    def test_health(self):
        r = self.c.get("/health")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertGreater(j["records"], 100)

    def test_home_renders(self):
        r = self.c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Mandi Lens", r.data)

    def test_state_filter(self):
        r = self.c.get("/?state=West+Bengal")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(b"Bhubaneswar", r.data)

    def test_search(self):
        r = self.c.get("/?q=Potato")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Potato", r.data)

    def test_search_no_match(self):
        r = self.c.get("/?q=Unobtanium")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Nothing matched", r.data)

    def test_commodity_page(self):
        r = self.c.get("/commodity/Potato")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Price trend", r.data)

    def test_commodity_404(self):
        self.assertEqual(self.c.get("/commodity/Nope").status_code, 404)

    def test_api_prices(self):
        j = self.c.get("/api/prices?commodity=Potato&limit=3").get_json()
        self.assertEqual(len(j["results"]), 3)

    def test_api_limit_capped(self):
        j = self.c.get("/api/prices?limit=99999").get_json()
        self.assertLessEqual(len(j["results"]), 500)

    def test_api_states_and_commodities(self):
        self.assertTrue(len(self.c.get("/api/states").get_json()) > 0)
        self.assertTrue(len(self.c.get("/api/commodities").get_json()) > 0)

    def test_api_trend_and_spread(self):
        t = self.c.get("/api/trend/Potato").get_json()
        self.assertTrue(len(t) > 1)
        s = self.c.get("/api/spread/Potato").get_json()
        self.assertTrue(len(s) > 1)
        self.assertIn("change", s[0])

    def test_about(self):
        self.assertEqual(self.c.get("/about").status_code, 200)

    def test_cross_process_lock_blocks_second_worker(self):
        """Two gunicorn workers must not load the history at the same time."""
        from app import refresh
        self.assertTrue(refresh._acquire_db_lock(), "first worker should win")
        self.assertFalse(refresh._acquire_db_lock(), "second worker must stand by")
        refresh._release_db_lock()
        self.assertTrue(refresh._acquire_db_lock(), "lock must be reusable")
        refresh._release_db_lock()

    def test_refresh_retries_after_file_seed(self):
        """A file-seeded database must still be considered worth refreshing."""
        from app import refresh
        self.assertTrue(refresh.is_stale(),
                        "file-seeded data should trigger a live retry")

    def test_home_has_hero_3d(self):
        """The home page must carry a live 3D hero, not just the 3D tab."""
        body = self.c.get("/").get_data(as_text=True)
        self.assertIn('id="hero3d"', body)
        self.assertIn("three.min.js", body)
        # the hero's data payload must be present, not an empty array
        self.assertIn("avg_price", body)
        self.assertNotIn("const DATA = []", body)

    def test_explore_3d_page_renders(self):
        """The 3D page must serve real geometry data, not an empty canvas."""
        r = self.c.get("/explore")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn('id="x3d"', body)
        self.assertIn("three.min.js", body)
        self.assertIn("avg_price", body)
        self.assertIn("commodity", body)

    def test_explore_3d_is_in_nav(self):
        body = self.c.get("/").get_data(as_text=True)
        self.assertIn('href="/explore"', body)

    def test_three_js_is_served(self):
        r = self.c.get("/static/three.min.js")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.data), 100000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
