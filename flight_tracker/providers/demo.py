"""Offline demo source with synthetic prices. Use it to try the app without any account."""
from __future__ import annotations

import random
import time
import zlib

from ..models import Offer, Query, utc_now_iso
from .base import Provider

CABIN_MULTIPLIER = {"ECONOMY": 1.0, "PREMIUM_ECONOMY": 1.6, "BUSINESS": 3.5, "FIRST": 5.0}
CARRIERS = ["DL", "UA", "AA", "WN", "B6"]
VENDORS = ["Demo Travel A", "Demo Travel B"]


class DemoProvider(Provider):
    key = "demo"
    label = "Demo (synthetic prices, offline)"

    def search(self, query: Query) -> list[Offer]:
        # Seeded per route/date so results are stable within an hour, then drift hourly.
        seed_text = f"{query.origin}{query.destination}{query.depart_date}{query.return_date}{query.cabin}"
        rng = random.Random(zlib.crc32(seed_text.encode()) ^ int(time.time() // 3600))

        base = 120 + rng.random() * 400
        if query.return_date:
            base *= 1.9
        base *= CABIN_MULTIPLIER.get(query.cabin, 1.0)
        base *= query.adults + 0.75 * query.children

        offers: list[Offer] = []
        for carrier in rng.sample(CARRIERS, k=rng.randint(2, 4)):
            stops = rng.choice([0, 1, 1, 2])
            if query.max_stops is not None and stops > query.max_stops:
                continue
            price = round(base * (0.85 + rng.random() * 0.4) * (1.0 if stops == 0 else 0.9), 2)
            legs = [self._leg(rng, carrier, query.origin, query.destination, stops, "outbound")]
            if query.return_date:
                legs.append(self._leg(rng, carrier, query.destination, query.origin, stops, "return"))
            offers.append(Offer(
                provider=self.key, vendor=rng.choice(VENDORS),
                origin=query.origin, destination=query.destination,
                depart_date=query.depart_date.isoformat(),
                return_date=query.return_date.isoformat() if query.return_date else None,
                price=price, currency=query.currency, cabin=query.cabin,
                legs=legs, seats_left=rng.randint(1, 9),
                raw={"synthetic": True}, scraped_at=utc_now_iso(),
            ))
        return offers

    @staticmethod
    def _leg(rng: random.Random, carrier: str, origin: str, destination: str,
             stops: int, direction: str) -> dict:
        hour = rng.randint(5, 22)
        minute = rng.choice(["00", "15", "30", "45"])
        duration = rng.randint(180, 900)
        return {
            "direction": direction, "origin": origin, "destination": destination,
            "depart": f"{hour:02d}:{minute}", "arrive": None, "stops": stops,
            "duration_minutes": duration, "flights": [f"{carrier}{rng.randint(100, 2999)}"],
            "carriers": [carrier],
        }
