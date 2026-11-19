"""Tests for buy links, itinerary text, and best-per-trip selection. Run with:  python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flight_tracker.booking import (best_per_trip, buy_url, buy_url_is_exact, csv_extras,  # noqa: E402
                                    google_flights_url, itinerary_lines, leg_clock, leg_summary,
                                    split_clock)


def offer(**overrides):
    base = {
        "id": 1, "origin": "JFK", "destination": "LAX", "depart_date": "2026-12-05",
        "return_date": None, "price": 200.0, "currency": "USD", "raw": None,
        "legs": [], "scraped_at": "2026-11-18T20:00:00+00:00",
    }
    base.update(overrides)
    return base


AMADEUS_LEG = {
    "direction": "outbound", "origin": "JFK", "destination": "LAX",
    "depart": "2026-12-05T07:10:00", "arrive": "2026-12-05T10:05:00", "stops": 0,
    "duration_minutes": 355, "flights": ["DL123"], "carriers": ["DL"],
}


class BuyUrlTests(unittest.TestCase):
    def test_google_search_url_for_one_way_and_round_trip(self):
        one_way = google_flights_url("JFK", "LAX", "2026-12-05", None, "USD")
        self.assertIn("one+way", one_way)
        self.assertIn("curr=USD", one_way)
        round_trip = google_flights_url("JFK", "LAX", "2026-12-05", "2026-12-10", "EUR")
        self.assertIn("through+2026-12-10", round_trip)
        self.assertIn("curr=EUR", round_trip)

    def test_falls_back_to_google_search_without_provider_link(self):
        o = offer(return_date="2026-12-10")
        self.assertFalse(buy_url_is_exact(o))
        self.assertEqual(buy_url(o), google_flights_url("JFK", "LAX", "2026-12-05", "2026-12-10", "USD"))

    def test_uses_provider_booking_link_when_present(self):
        o = offer(raw={"booking_url": "https://example.test/fare/123"})
        self.assertTrue(buy_url_is_exact(o))
        self.assertEqual(buy_url(o), "https://example.test/fare/123")


class ItineraryTests(unittest.TestCase):
    def test_split_clock_handles_iso_and_bare_times(self):
        self.assertEqual(split_clock("2026-12-05T07:10:00"), ("2026-12-05", "07:10"))
        self.assertEqual(split_clock("09:45"), ("", "09:45"))
        self.assertEqual(split_clock(None), ("", ""))

    def test_leg_summary_includes_times_flight_and_carrier(self):
        text = leg_summary(AMADEUS_LEG)
        for part in ("Outbound:", "JFK 07:10", "LAX 10:05", "2026-12-05", "5h 55m", "nonstop",
                     "DL123", "[DL]"):
            self.assertIn(part, text)

    def test_leg_summary_marks_next_day_arrival(self):
        leg = dict(AMADEUS_LEG, arrive="2026-12-06T01:30:00", stops=1)
        self.assertIn("(+1d)", leg_summary(leg))
        self.assertIn("1 stop", leg_summary(leg))

    def test_missing_details_are_labelled_not_invented(self):
        text = leg_summary({"direction": "outbound", "origin": "JFK", "destination": "LAX",
                            "depart": "07:10", "arrive": None, "stops": None,
                            "duration_minutes": None, "flights": [], "carriers": []})
        self.assertIn("stops n/a", text)
        self.assertIn("flight # n/a", text)
        self.assertIn("airline n/a", text)

    def test_leg_clock_returns_outbound_and_return_times(self):
        o = offer(legs=[AMADEUS_LEG, dict(AMADEUS_LEG, depart="2026-12-10T16:20:00")])
        self.assertEqual(leg_clock(o, 0), "07:10")
        self.assertEqual(leg_clock(o, 1), "16:20")
        self.assertEqual(leg_clock(offer(), 1), "")
        self.assertEqual(len(itinerary_lines(o)), 2)


class CsvExtrasTests(unittest.TestCase):
    def test_extras_include_times_and_link_flag(self):
        o = offer(legs=[AMADEUS_LEG])
        extras = csv_extras(o)
        self.assertEqual(extras["outbound_depart"], "07:10")
        self.assertEqual(extras["return_depart"], "")
        self.assertEqual(extras["buy_url_exact"], "no")
        self.assertTrue(extras["buy_url"].startswith("https://www.google.com/travel/flights?q="))


class BestPerTripTests(unittest.TestCase):
    def test_keeps_cheapest_offer_per_route_and_dates(self):
        offers = [
            offer(id=1, price=300.0),
            offer(id=2, price=250.0),                       # same trip, cheaper
            offer(id=3, price=260.0, depart_date="2026-12-06"),  # different date
            offer(id=4, price=240.0, return_date="2026-12-10"),  # round trip is a different trip
        ]
        best = best_per_trip(offers)
        self.assertEqual([o["id"] for o in best], [4, 2, 3])  # ordered by price
        self.assertEqual(len(best), 3)

    def test_empty_input(self):
        self.assertEqual(best_per_trip([]), [])


if __name__ == "__main__":
    unittest.main()
