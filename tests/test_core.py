"""Tests for the core logic. Run with:  python -m unittest discover -s tests -v"""
import json
import os
import queue
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flight_tracker.airports import resolve_airports  # noqa: E402
from flight_tracker.models import Offer, Query, SearchSpec  # noqa: E402
from flight_tracker.providers.amadeus import parse_iso_duration, parse_offer  # noqa: E402
from flight_tracker.providers.demo import DemoProvider  # noqa: E402
from flight_tracker.providers.google_flights import parse_result_text  # noqa: E402
from flight_tracker.scheduler import TrackerService  # noqa: E402
from flight_tracker.storage import Store  # noqa: E402

FUTURE = date.today() + timedelta(days=60)


def last_event(events: queue.Queue) -> dict:
    """Return the final run_done event, skipping progress events."""
    items = []
    while not events.empty():
        items.append(events.get_nowait())
    return [e for e in items if e["type"] == "run_done"][-1]


def make_spec(**overrides) -> SearchSpec:
    base = dict(name="Test", origins=["JFK"], destinations=["LAX"],
                depart_date=FUTURE, providers=["demo"])
    base.update(overrides)
    return SearchSpec(**base)


class AirportTests(unittest.TestCase):
    def test_iata_icao_and_city(self):
        self.assertEqual(resolve_airports("jfk"), ["JFK"])
        self.assertEqual(resolve_airports("KLAX"), ["LAX"])
        self.assertEqual(sorted(resolve_airports("New York")), ["EWR", "JFK", "LGA"])
        self.assertEqual(resolve_airports("NYC, LAX"), ["EWR", "JFK", "LGA", "LAX"])

    def test_unknown_city_is_an_error(self):
        with self.assertRaises(ValueError):
            resolve_airports("Atlantis Central")


class DateExpansionTests(unittest.TestCase):
    def test_strict_round_trip_is_one_query_per_route(self):
        spec = make_spec(return_date=FUTURE + timedelta(days=7))
        queries = spec.queries()
        self.assertEqual(len(queries), 1)
        self.assertEqual(queries[0].return_date, FUTURE + timedelta(days=7))

    def test_flexible_window_expands_days_times_lengths(self):
        spec = make_spec(date_mode="flexible", depart_date=None,
                         depart_from=FUTURE, depart_to=FUTURE + timedelta(days=2),
                         nights_min=3, nights_max=4, max_date_combinations=100)
        pairs = spec.date_pairs()
        self.assertEqual(len(pairs), 3 * 2)
        self.assertTrue(all((r - d).days in (3, 4) for d, r in pairs))

    def test_flexible_cap_limits_combinations(self):
        spec = make_spec(date_mode="flexible", depart_date=None,
                         depart_from=FUTURE, depart_to=FUTURE + timedelta(days=60),
                         nights_min=None, nights_max=None, max_date_combinations=10)
        self.assertLessEqual(len(spec.date_pairs()), 10)

    def test_multi_airport_routes_multiply(self):
        spec = make_spec(origins=["JFK", "EWR"], destinations=["LAX", "SFO"])
        self.assertEqual(len(spec.queries()), 4)

    def test_validation_catches_bad_input(self):
        with self.assertRaises(ValueError) as ctx:
            make_spec(depart_date=date(2000, 1, 1)).validate()
        self.assertIn("past", str(ctx.exception))
        with self.assertRaises(ValueError):
            make_spec(destinations=["JFK"]).validate()  # same as origin
        with self.assertRaises(ValueError):
            make_spec(interval_minutes=1).validate()

    def test_json_round_trip(self):
        spec = make_spec(return_date=FUTURE + timedelta(days=5), budget=300.0,
                         include_airlines=["DL"], max_stops=1)
        restored = SearchSpec.from_json(spec.to_json())
        self.assertEqual(restored, spec)


class FilterTests(unittest.TestCase):
    def _offer(self, carriers, stops):
        return Offer("x", "v", "JFK", "LAX", "2026-12-01", None, 100.0, "USD", "ECONOMY",
                     legs=[{"stops": stops, "carriers": carriers, "flights": [],
                            "duration_minutes": 300}])

    def test_stop_filter(self):
        spec = make_spec(max_stops=0)
        self.assertTrue(spec.allows(self._offer(["DL"], 0)))
        self.assertFalse(spec.allows(self._offer(["DL"], 1)))

    def test_airline_include_exclude(self):
        self.assertTrue(make_spec(include_airlines=["DL"]).allows(self._offer(["DL"], 0)))
        self.assertFalse(make_spec(include_airlines=["DL"]).allows(self._offer(["DL", "UA"], 0)))
        self.assertFalse(make_spec(exclude_airlines=["UA"]).allows(self._offer(["UA"], 0)))


class AmadeusParsingTests(unittest.TestCase):
    ITEM = {
        "id": "1", "numberOfBookableSeats": 4, "validatingAirlineCodes": ["BA"],
        "price": {"currency": "USD", "grandTotal": "412.50"},
        "itineraries": [
            {"duration": "PT7H30M", "segments": [
                {"carrierCode": "BA", "number": "117",
                 "departure": {"iataCode": "JFK", "at": "2026-12-01T19:00:00"},
                 "arrival": {"iataCode": "LHR", "at": "2026-12-02T07:00:00"}},
            ]},
            {"duration": "PT10H", "segments": [
                {"carrierCode": "BA", "number": "2",
                 "departure": {"iataCode": "LHR", "at": "2026-12-09T10:00:00"},
                 "arrival": {"iataCode": "JFK", "at": "2026-12-09T13:00:00"}},
            ]},
        ],
    }

    def test_duration_parser(self):
        self.assertEqual(parse_iso_duration("PT2H30M"), 150)
        self.assertEqual(parse_iso_duration("PT45M"), 45)
        self.assertIsNone(parse_iso_duration(None))

    def test_offer_fields(self):
        q = Query("JFK", "LHR", date(2026, 12, 1), date(2026, 12, 9), 1, 0, "ECONOMY", "USD", None)
        offer = parse_offer(self.ITEM, q)
        self.assertEqual(offer.price, 412.50)
        self.assertEqual(offer.currency, "USD")
        self.assertEqual(offer.airlines, ["BA"])
        self.assertEqual(offer.flight_numbers, ["BA117", "BA2"])
        self.assertEqual(offer.outbound_stops, 0)
        self.assertEqual(offer.duration_minutes, 450 + 600)
        self.assertIn("ticketed by BA", offer.vendor)
        self.assertEqual(offer.seats_left, 4)


class GoogleParsingTests(unittest.TestCase):
    def test_parses_row_text(self):
        q = Query("JFK", "NRT", date(2026, 12, 1), None, 1, 0, "ECONOMY", "USD", None)
        text = "7:05 AM – 3:40 PM+1\nDelta, Japan Airlines\n1 stop\n21 hr 35 min\n$1,245"
        offer = parse_result_text(text, q)
        self.assertEqual(offer.price, 1245.0)
        self.assertEqual(offer.outbound_stops, 1)
        self.assertEqual(offer.duration_minutes, 21 * 60 + 35)
        self.assertEqual(sorted(offer.airlines), ["DL", "JL"])

    def test_non_result_rows_are_ignored(self):
        q = Query("JFK", "NRT", date(2026, 12, 1), None, 1, 0, "ECONOMY", "USD", None)
        self.assertIsNone(parse_result_text("Sort by: Price", q))

    def test_duplicated_dom_times_do_not_become_the_arrival(self):
        q = Query("DEN", "SRQ", date(2026, 11, 23), None, 1, 0, "ECONOMY", "USD", None)
        text = "1:15 PM1:15 PM on Mon, Nov 23 – 6:35 PM6:35 PM on Mon, Nov 23\nSouthwest\n3 hr 20 min\nNonstop\n$997"
        leg = parse_result_text(text, q).legs[0]
        self.assertEqual(leg["depart"], "1:15 PM")
        self.assertEqual(leg["arrive"], "6:35 PM")


class StorageAndServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_track_crud_and_metadata(self):
        spec = make_spec(budget=500)
        tid = self.store.add_track(spec.name, spec)
        track = self.store.get_track(tid)
        self.assertEqual(track["spec"].budget, 500)
        self.assertTrue(track["active"])
        self.store.set_track_active(tid, False)
        self.assertFalse(self.store.get_track(tid)["active"])
        self.store.delete_track(tid)
        self.assertIsNone(self.store.get_track(tid))

    def test_demo_run_stores_offers_and_alerts(self):
        spec = make_spec(budget=100000)  # generous budget guarantees an alert
        tid = self.store.add_track(spec.name, spec)
        events = queue.Queue()
        service = TrackerService(self.store, events)
        service._execute(tid, "manual")
        event = last_event(events)
        self.assertEqual(event["type"], "run_done")
        self.assertGreater(event["offers"], 0)
        self.assertTrue(any("Under budget" in a for a in event["alerts"]))
        rows = self.store.latest_offers(tid)
        self.assertEqual(len(rows), event["offers"])
        self.assertEqual(rows[0]["provider"], "demo")
        self.assertIsInstance(rows[0]["legs"], list)
        runs = self.store.list_runs(tid)
        self.assertEqual(runs[0]["status"], "ok")
        self.assertEqual(len(self.store.price_history(tid)), 1)

    def test_missing_credentials_are_reported_not_crashed(self):
        spec = make_spec(providers=["amadeus"])
        tid = self.store.add_track(spec.name, spec)
        events = queue.Queue()
        with mock.patch.dict(os.environ, {}, clear=True):
            TrackerService(self.store, events)._execute(tid, "manual")
        event = last_event(events)
        self.assertEqual(event["offers"], 0)
        self.assertTrue(any("missing settings" in e for e in event["errors"]))
        self.assertEqual(self.store.list_runs(tid)[0]["status"], "error")

    def test_provider_exception_is_recorded(self):
        spec = make_spec()
        tid = self.store.add_track(spec.name, spec)
        events = queue.Queue()
        with mock.patch.object(DemoProvider, "search", side_effect=RuntimeError("boom")):
            TrackerService(self.store, events)._execute(tid, "manual")
        event = last_event(events)
        self.assertTrue(any("boom" in e for e in event["errors"]))


if __name__ == "__main__":
    unittest.main()
