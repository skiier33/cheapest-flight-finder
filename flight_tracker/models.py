"""Core data types: search specifications, provider queries, and price offers."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from itertools import product
from typing import Any

CABINS = ("ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST")
DATE_FIELDS = ("depart_date", "return_date", "depart_from", "depart_to")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Query:
    """One concrete search: a single route on a single set of dates."""
    origin: str
    destination: str
    depart_date: date
    return_date: date | None  # None means one-way
    adults: int
    children: int
    cabin: str
    currency: str
    max_stops: int | None


@dataclass
class Offer:
    """One priced itinerary returned by a provider."""
    provider: str
    vendor: str                      # who sells/ticket it (e.g. "Amadeus GDS", "Google Flights")
    origin: str
    destination: str
    depart_date: str
    return_date: str | None
    price: float
    currency: str
    cabin: str
    # Each leg: direction, origin, destination, depart, arrive, stops,
    # duration_minutes, flights (e.g. "DL123"), carriers (e.g. "DL")
    legs: list[dict[str, Any]] = field(default_factory=list)
    seats_left: int | None = None
    raw: Any = None
    scraped_at: str = ""

    @property
    def airlines(self) -> list[str]:
        return sorted({c for leg in self.legs for c in leg.get("carriers", [])})

    @property
    def flight_numbers(self) -> list[str]:
        return [f for leg in self.legs for f in leg.get("flights", [])]

    @property
    def outbound_stops(self) -> int | None:
        return self.legs[0].get("stops") if self.legs else None

    @property
    def return_stops(self) -> int | None:
        return self.legs[1].get("stops") if len(self.legs) > 1 else None

    @property
    def duration_minutes(self) -> int | None:
        durations = [leg.get("duration_minutes") for leg in self.legs]
        if durations and all(d is not None for d in durations):
            return sum(durations)
        return None


def sample_evenly(items: list, limit: int) -> list:
    """Keep at most `limit` items, spread evenly across the list."""
    if len(items) <= limit:
        return items
    step = math.ceil(len(items) / limit)
    return items[::step][:limit]


@dataclass
class SearchSpec:
    """Everything the user configured for one tracker."""
    name: str
    origins: list[str]
    destinations: list[str]
    date_mode: str = "strict"                 # "strict" or "flexible"
    depart_date: date | None = None           # strict
    return_date: date | None = None           # strict; None = one-way
    depart_from: date | None = None           # flexible window start
    depart_to: date | None = None             # flexible window end
    nights_min: int | None = None             # flexible trip length; None = one-way
    nights_max: int | None = None
    adults: int = 1
    children: int = 0
    cabin: str = "ECONOMY"
    max_stops: int | None = None              # None = any, 0 = nonstop only
    include_airlines: list[str] = field(default_factory=list)
    exclude_airlines: list[str] = field(default_factory=list)
    budget: float | None = None               # alert when price <= budget
    currency: str = "USD"
    providers: list[str] = field(default_factory=lambda: ["demo"])
    interval_minutes: int = 360
    max_date_combinations: int = 30           # cap per route, keeps API/scrape volume sane

    def validate(self) -> None:
        errors: list[str] = []
        today = date.today()
        if not self.name.strip():
            errors.append("Give the tracker a name.")
        if not self.origins or not self.destinations:
            errors.append("Enter at least one origin and one destination.")
        overlap = set(self.origins) & set(self.destinations)
        if overlap:
            errors.append(f"Origin and destination overlap: {', '.join(sorted(overlap))}.")

        if self.date_mode == "strict":
            if not self.depart_date:
                errors.append("Strict mode needs a departure date.")
            elif self.depart_date < today:
                errors.append("Departure date is in the past.")
            elif self.return_date and self.return_date < self.depart_date:
                errors.append("Return date is before the departure date.")
        elif self.date_mode == "flexible":
            if not self.depart_from or not self.depart_to:
                errors.append("Flexible mode needs a departure window (from and to).")
            elif self.depart_to < self.depart_from:
                errors.append("Departure window 'to' is before 'from'.")
            elif self.depart_to < today:
                errors.append("Departure window is entirely in the past.")
            if (self.nights_min is None) != (self.nights_max is None):
                errors.append("Set both minimum and maximum trip nights, or neither (one-way).")
            elif self.nights_min is not None and not (1 <= self.nights_min <= self.nights_max):
                errors.append("Trip nights must satisfy 1 <= min <= max.")
        else:
            errors.append("Unknown date mode.")

        if self.adults < 1:
            errors.append("At least one adult is required.")
        if self.children < 0:
            errors.append("Children can't be negative.")
        if self.cabin not in CABINS:
            errors.append("Pick a valid cabin class.")
        if self.max_stops not in (None, 0, 1, 2):
            errors.append("Max stops must be any, 0, 1 or 2.")
        if self.interval_minutes < 5:
            errors.append("Check interval must be at least 5 minutes.")
        if self.max_date_combinations < 1:
            errors.append("Max date combinations must be at least 1.")
        if not self.providers:
            errors.append("Select at least one price source.")
        if errors:
            raise ValueError("\n".join(errors))

    def date_pairs(self) -> list[tuple[date, date | None]]:
        """Expand the date settings into (depart, return) pairs for one route."""
        if self.date_mode == "strict":
            return [(self.depart_date, self.return_date)]
        pairs: list[tuple[date, date | None]] = []
        day = self.depart_from
        while day <= self.depart_to:
            if self.nights_min is None:
                pairs.append((day, None))
            else:
                for nights in range(self.nights_min, self.nights_max + 1):
                    pairs.append((day, day + timedelta(days=nights)))
            day += timedelta(days=1)
        return sample_evenly(pairs, self.max_date_combinations)

    def queries(self) -> list[Query]:
        pairs = self.date_pairs()
        return [
            Query(origin, dest, dep, ret, self.adults, self.children,
                  self.cabin, self.currency, self.max_stops)
            for origin, dest in product(self.origins, self.destinations)
            for dep, ret in pairs
        ]

    def allows(self, offer: Offer) -> bool:
        """Apply stop and airline filters. Price budget is alert-only, not a filter."""
        if self.max_stops is not None:
            for leg in offer.legs:
                stops = leg.get("stops")
                if stops is not None and stops > self.max_stops:
                    return False
        airlines = set(offer.airlines)
        if airlines:
            if self.include_airlines and not airlines <= set(self.include_airlines):
                return False
            if self.exclude_airlines and airlines & set(self.exclude_airlines):
                return False
        return True

    def to_json(self) -> str:
        data = {}
        for key, value in self.__dict__.items():
            data[key] = value.isoformat() if isinstance(value, date) else value
        return json.dumps(data)

    @classmethod
    def from_json(cls, text: str) -> "SearchSpec":
        data = json.loads(text)
        for key in DATE_FIELDS:
            if data.get(key):
                data[key] = date.fromisoformat(data[key])
        return cls(**data)
