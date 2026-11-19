"""Tests for pricing round trips as two one-way tickets. Run with:  python -m unittest discover -s tests -v"""
import queue
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flight_tracker.booking import best_per_trip, buy_links, combo_savings  # noqa: E402
from flight_tracker.combos import build_one_way_combos  # noqa: E402
from flight_tracker.models import COMBO_PROVIDER, Offer, Query, SearchSpec  # noqa: E402
from flight_tracker.scheduler import TrackerService  # noqa: E402
from flight_tracker.storage import Store  # noqa: E402

D1 = date.today() + timedelta(days=60)
D2 = D1 + timedelta(days=7)


def leg_offer(provider, origin, dest, day, price, carrier="DL"):
    return Offer(provider, f"{provider} vendor", origin, dest, day.isoformat(), None, price, "USD",
                 "ECONOMY", legs=[{"direction": "outbound", "origin": origin, "destination": dest,
                                   "depart": "08:00", "arrive": "11:00", "stops": 0,
                                   "duration_minutes": 180, "flights": [f"{carrier}1"],
                                   "carriers": [carrier]}])


def rt_offer(provider, price, origin="JFK", dest="LAX", dep=D1, ret=D2):
    return Offer(provider, "rt vendor", origin, dest, dep.isoformat(), ret.isoformat(), price, "USD",
                 "ECONOMY", legs=[
                     {"direction": "outbound", "origin": origin, "destination": dest, "depart": "08:00",
                      "arrive": "11:00", "stops": 0, "duration_minutes": 180, "flights": [], "carriers": ["DL"]},
                     {"direction": "return", "origin": dest, "destination": origin, "depart": "09:00",
                      "arrive": "17:00", "stops": 0, "duration_minutes": 300, "flights": [], "carriers": ["DL"]},
                 ])


def round_trip_query(dep=D1, ret=D2, origin="JFK", dest="LAX"):
    return Query(origin, dest, dep, ret, 1, 0, "ECONOMY", "USD", None)


def make_spec(**overrides):
    base = dict(name="Combo", origins=["JFK"], destinations=["LAX"], depart_date=D1,
                return_date=D2, providers=["demo"], one_way_combos=True)
    base.update(overrides)
    return SearchSpec(**base)


class OneWayQueryTests(unittest.TestCase):
    def test_round_trip_splits_into_two_one_way_legs(self):
        legs = make_spec().one_way_queries()
        self.assertEqual({(q.origin, q.destination, q.depart_date) for q in legs},
                         {("JFK", "LAX", D1), ("LAX", "JFK", D2)})
        self.assertTrue(all(q.return_date is None for q in legs))

    def test_flexible_legs_are_shared_between_date_pairs(self):
        spec = make_spec(date_mode="flexible", depart_date=None, return_date=None,
                         depart_from=D1, depart_to=D1 + timedelta(days=1),
                         nights_min=7, nights_max=8, max_date_combinations=100)
        legs = spec.one_way_queries()
        outbound_days = {q.depart_date for q in legs if q.origin == "JFK"}
        return_days = {q.depart_date for q in legs if q.origin == "LAX"}
        self.assertEqual(outbound_days, {D1, D1 + timedelta(days=1)})
        self.assertEqual(return_days, {D1 + timedelta(days=7), D1 + timedelta(days=8),
                                       D1 + timedelta(days=9)})
        self.assertEqual(len(legs), len(outbound_days) + len(return_days))

    def test_no_legs_when_disabled_or_one_way(self):
        self.assertEqual(make_spec(one_way_combos=False).one_way_queries(), [])
        self.assertEqual(make_spec(return_date=None).one_way_queries(), [])

    def test_flag_survives_json_round_trip(self):
        spec = make_spec()
        self.assertTrue(SearchSpec.from_json(spec.to_json()).one_way_combos)


class CombineTests(unittest.TestCase):
    def test_cheapest_legs_are_combined_across_sources(self):
        legs = [leg_offer("amadeus", "JFK", "LAX", D1, 300.0),
                leg_offer("demo", "JFK", "LAX", D1, 250.0),
                leg_offer("demo", "LAX", "JFK", D2, 180.0, carrier="UA")]
        rt = [rt_offer("demo", 600.0)]
        (combo,) = build_one_way_combos([round_trip_query()], rt, legs)
        self.assertEqual(combo.provider, COMBO_PROVIDER)
        self.assertEqual(combo.price, 430.0)
        self.assertEqual(combo.raw["outbound"]["provider"], "demo")
        self.assertEqual(combo.raw["return"]["provider"], "demo")
        self.assertEqual(combo.raw["round_trip_best"], 600.0)
        self.assertEqual(combo.raw["savings"], 170.0)
        self.assertEqual(combo_savings({"provider": COMBO_PROVIDER, "raw": combo.raw}), 170.0)
        self.assertEqual([leg["direction"] for leg in combo.legs], ["outbound", "return"])
        self.assertEqual(combo.legs[1]["carriers"], ["UA"])
        self.assertEqual(combo.return_date, D2.isoformat())

    def test_combo_costlier_than_round_trip_has_negative_savings(self):
        legs = [leg_offer("demo", "JFK", "LAX", D1, 300.0), leg_offer("demo", "LAX", "JFK", D2, 300.0)]
        (combo,) = build_one_way_combos([round_trip_query()], [rt_offer("demo", 500.0)], legs)
        self.assertEqual(combo.raw["savings"], -100.0)

    def test_no_round_trip_price_means_no_savings(self):
        legs = [leg_offer("demo", "JFK", "LAX", D1, 300.0), leg_offer("demo", "LAX", "JFK", D2, 300.0)]
        (combo,) = build_one_way_combos([round_trip_query()], [], legs)
        self.assertIsNone(combo.raw["savings"])

    def test_missing_return_leg_produces_no_combo(self):
        legs = [leg_offer("demo", "JFK", "LAX", D1, 300.0)]
        self.assertEqual(build_one_way_combos([round_trip_query()], [], legs), [])


class BookingTests(unittest.TestCase):
    def test_combo_has_two_links_and_is_exact_only_when_both_are(self):
        raw = {"outbound": {"booking_url": "https://a.example/out"}, "return": {"booking_url": None}}
        combo = {"provider": COMBO_PROVIDER, "origin": "JFK", "destination": "LAX",
                 "depart_date": D1.isoformat(), "return_date": D2.isoformat(), "currency": "USD", "raw": raw}
        links = buy_links(combo)
        self.assertEqual([exact for _l, _u, exact in links], [True, False])
        self.assertEqual(links[0][1], "https://a.example/out")
        self.assertIn("LAX", links[1][1])  # return ticket falls back to a Google search for LAX -> JFK

    def test_best_per_trip_keeps_combos_apart_from_round_trips(self):
        rt = {"origin": "JFK", "destination": "LAX", "depart_date": "d", "return_date": "r",
              "currency": "USD", "price": 600.0, "provider": "demo", "raw": None}
        combo = dict(rt, price=430.0, provider=COMBO_PROVIDER)
        shown = best_per_trip([rt, combo])
        self.assertEqual(len(shown), 2)
        self.assertEqual({o["provider"] for o in shown}, {"demo", COMBO_PROVIDER})


class SchedulerComboTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "combo.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def run_track(self, spec):
        tid = self.store.add_track(spec.name, spec)
        events = queue.Queue()
        TrackerService(self.store, events)._execute(tid, "manual")
        done = [e for e in list(events.queue) if e["type"] == "run_done"][-1]
        return tid, done

    def test_run_stores_combos_but_not_loose_one_way_legs(self):
        tid, event = self.run_track(make_spec(budget=None))
        rows = self.store.latest_offers(tid)
        combos = [r for r in rows if r["provider"] == COMBO_PROVIDER]
        self.assertEqual(len(combos), 1)
        self.assertTrue(all(r["return_date"] for r in rows), "one-way legs must not be stored")
        combo = combos[0]
        self.assertEqual(combo["raw"]["kind"], COMBO_PROVIDER)
        out_price = combo["raw"]["outbound"]["price"]
        ret_price = combo["raw"]["return"]["price"]
        self.assertAlmostEqual(combo["price"], round(out_price + ret_price, 2))
        self.assertEqual(combo["depart_date"], D1.isoformat())
        self.assertEqual(combo["return_date"], D2.isoformat())

    def test_history_and_min_price_ignore_combos(self):
        spec = make_spec()
        tid, event = self.run_track(spec)
        rows = self.store.latest_offers(tid)
        combo = next(r for r in rows if r["provider"] == COMBO_PROVIDER)
        savings = combo["raw"]["savings"]
        if savings is not None and savings > 0:
            self.assertTrue(any("two one-way" in a.lower() for a in event["alerts"]))
        # Price history reflects round-trip prices only.
        rt_min = min(r["price"] for r in rows if r["provider"] != COMBO_PROVIDER)
        self.assertAlmostEqual(self.store.price_history(tid)[0][1], rt_min)
        self.assertEqual(self.store.min_price(tid), rt_min)

    def test_flag_off_searches_round_trips_only(self):
        tid, _ = self.run_track(make_spec(one_way_combos=False))
        rows = self.store.latest_offers(tid)
        self.assertTrue(rows)
        self.assertTrue(all(r["provider"] != COMBO_PROVIDER for r in rows))


if __name__ == "__main__":
    unittest.main()
