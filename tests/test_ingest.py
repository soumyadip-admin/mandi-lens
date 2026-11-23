"""Tests for the data-cleaning layer — the part that actually breaks in real life."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ingest.fetch import parse_date, parse_price, build_keymap, normalise, parse_payload  # noqa


class TestParseDate(unittest.TestCase):
    def test_dd_mm_yyyy_is_indian_order(self):
        # 03/04/2026 must be 3 April, not 4 March
        self.assertEqual(parse_date("03/04/2026"), "2026-04-03")

    def test_iso_passthrough(self):
        self.assertEqual(parse_date("2026-11-21"), "2026-11-21")

    def test_iso_with_time(self):
        self.assertEqual(parse_date("2026-11-21T09:30:00"), "2026-11-21")

    def test_month_name(self):
        self.assertEqual(parse_date("21 Nov 2026"), "2026-11-21")

    def test_blank_and_garbage(self):
        for v in (None, "", "   ", "not a date", "N/A"):
            self.assertIsNone(parse_date(v), v)

    def test_impossible_date(self):
        self.assertIsNone(parse_date("45/13/2026"))


class TestParsePrice(unittest.TestCase):
    def test_comma_separated(self):
        self.assertEqual(parse_price("1,250"), 1250.0)

    def test_rupee_symbol(self):
        self.assertEqual(parse_price("₹1,250"), 1250.0)

    def test_decimal(self):
        self.assertEqual(parse_price("1250.50"), 1250.5)

    def test_placeholders_become_none(self):
        for v in ("NR", "NA", "N/A", "-", "", None):
            self.assertIsNone(parse_price(v), v)

    def test_negative_rejected(self):
        self.assertIsNone(parse_price("-5"))

    def test_float_string(self):
        self.assertEqual(parse_price("1250.0"), 1250.0)


class TestKeyMapping(unittest.TestCase):
    def test_snake_case(self):
        km = build_keymap({"state": 1, "commodity": 1, "modal_price": 1})
        self.assertEqual(km["state"], "state")
        self.assertEqual(km["modal_price"], "modal_price")

    def test_spaces_and_caps(self):
        km = build_keymap({"State Name": 1, "Commodity Name": 1, "Modal Price": 1})
        self.assertEqual(km["state"], "State Name")
        self.assertEqual(km["commodity"], "Commodity Name")
        self.assertEqual(km["modal_price"], "Modal Price")

    def test_alias_variant(self):
        km = build_keymap({"statename": 1, "crop": 1, "date": 1})
        self.assertEqual(km["state"], "statename")
        self.assertEqual(km["commodity"], "crop")
        self.assertEqual(km["arrival_date"], "date")


class TestNormalise(unittest.TestCase):
    def test_clean_record(self):
        rows, rej = normalise([{
            "state": "West Bengal", "district": "Kolkata", "market": "Posta Bazar",
            "commodity": "Potato", "variety": "Local", "grade": "A",
            "arrival_date": "21/11/2026", "min_price": "1,000",
            "max_price": "1,300", "modal_price": "1,150"}], "test")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rej, 0)
        self.assertEqual(rows[0]["arrival_date"], "2026-11-21")
        self.assertEqual(rows[0]["modal_price"], 1150.0)

    def test_missing_modal_is_derived_from_min_max(self):
        rows, _ = normalise([{
            "state": "Bihar", "market": "Patna City", "commodity": "Rice",
            "arrival_date": "21/11/2026", "min_price": "100", "max_price": "200",
            "modal_price": "NR"}], "test")
        self.assertEqual(rows[0]["modal_price"], 150.0)

    def test_unusable_row_rejected(self):
        rows, rej = normalise([{"state": "Bihar", "commodity": "Rice",
                                "arrival_date": "garbage",
                                "modal_price": "100"}], "test")
        self.assertEqual(len(rows), 0)
        self.assertEqual(rej, 1)

    def test_missing_variety_defaults_empty(self):
        rows, _ = normalise([{"state": "Bihar", "market": "M", "commodity": "Rice",
                              "arrival_date": "21/11/2026", "modal_price": "1000"}], "test")
        self.assertEqual(rows[0]["variety"], "")


class TestPayloadParsing(unittest.TestCase):
    def test_json_object(self):
        recs, total = parse_payload('{"records":[{"a":1}],"total":1}', "application/json")
        self.assertEqual(len(recs), 1)
        self.assertEqual(total, 1)

    def test_csv_fallback(self):
        recs, total = parse_payload("state,commodity\nBihar,Rice\n", "text/csv")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["state"], "Bihar")

    def test_empty_body(self):
        recs, total = parse_payload("", "")
        self.assertEqual(recs, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
