"""Tests for the CSV chart page builder. Run with:  python -m unittest discover -s tests -v"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flight_tracker.visualize import build_html, load_rows, write_page  # noqa: E402

HEADER = ("scraped_at,provider,vendor,origin,destination,depart_date,return_date,price,currency,"
          "airlines,flight_numbers,outbound_stops,return_stops,duration_minutes,cabin,seats_left\n")


class LoadRowsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_csv(self, body: str, header: str = HEADER) -> Path:
        path = self.dir / "prices.csv"
        path.write_text(header + body, encoding="utf-8")
        return path

    def test_parses_export_columns(self):
        path = self.write_csv(
            "2026-11-18T20:00:00+00:00,google_flights,Google Flights,JFK,LAX,2026-12-05,,"
            "199.5,USD,\"DL,UA\",\"DL123,UA456\",0,,300,ECONOMY,\n")
        rows = load_rows(path)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["price"], 199.5)
        self.assertEqual(row["airlines"], "DL, UA")
        self.assertEqual(row["flights"], "DL123 · UA456")
        self.assertEqual(row["stops"], 0)
        self.assertEqual(row["ret"], "")
        self.assertEqual(row["t"], 1795032000000)  # 2026-11-18T20:00:00Z in epoch ms
        self.assertIsInstance(row["t"], int)

    def test_skips_rows_without_price_or_time(self):
        path = self.write_csv(
            "2026-11-18T20:00:00+00:00,p,v,JFK,LAX,2026-12-05,,,USD,,,,,,,\n"
            "not-a-time,p,v,JFK,LAX,2026-12-05,,100,USD,,,,,,,\n"
            "2026-11-18T20:00:00+00:00,p,v,JFK,LAX,2026-12-05,,80,USD,,,,,,,\n")
        rows = load_rows(path)
        self.assertEqual([r["price"] for r in rows], [80.0])

    def test_naive_timestamps_are_treated_as_utc(self):
        path = self.write_csv("2026-11-18T20:00:00,p,v,JFK,LAX,2026-12-05,,80,USD,,,,,,,\n")
        aware = self.write_csv("2026-11-18T20:00:00+00:00,p,v,JFK,LAX,2026-12-05,,80,USD,,,,,,,\n")
        self.assertEqual(load_rows(path)[0]["t"], load_rows(aware)[0]["t"])

    def test_rejects_files_that_are_not_exports(self):
        path = self.dir / "other.csv"
        path.write_text("name,value\nx,1\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_rows(path)


class BuildHtmlTests(unittest.TestCase):
    def test_embedded_data_cannot_close_the_script_tag(self):
        rows = [{"t": 1, "price": 10.0, "currency": "USD", "vendor": "</script><b>x</b>",
                 "origin": "JFK", "destination": "LAX", "depart": "2026-12-05", "ret": "",
                 "flights": "", "airlines": "", "stops": None, "duration": None, "cabin": ""}]
        page = build_html(rows, "Test <title>")
        scripts = re.findall(r"<script>(.*?)</script>", page, flags=re.S)
        self.assertEqual(len(scripts), 1)
        self.assertNotIn("</script><b>", page)
        self.assertIn("Test &lt;title&gt;", page)
        data_literal = re.search(r"var DATA = (.*?);\n", scripts[0], flags=re.S).group(1)
        self.assertEqual(json.loads(data_literal)[0]["vendor"], "</script><b>x</b>")

    def test_write_page_defaults_next_to_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "run.csv"
            csv_path.write_text(HEADER + "2026-11-18T20:00:00+00:00,p,v,JFK,LAX,2026-12-05,,80,USD,,,,,,,\n",
                                encoding="utf-8")
            out = write_page(csv_path)
            self.assertEqual(out, Path(tmp) / "run.html")
            self.assertIn("Flight prices: run.csv", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
