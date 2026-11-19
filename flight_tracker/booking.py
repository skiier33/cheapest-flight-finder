"""Buy links, itinerary summaries, and best-per-trip selection for stored offers.

Offers are plain dicts as returned by Store.latest_offers (with `legs` and `raw` decoded).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

GOOGLE_FLIGHTS = "https://www.google.com/travel/flights"


def google_flights_url(origin: str, destination: str, depart_date: str,
                       return_date: str | None, currency: str | None) -> str:
    """Google Flights search for one route and date pair (not a fixed fare)."""
    text = f"Flights to {destination} from {origin} on {depart_date}"
    text += f" through {return_date}" if return_date else " one way"
    return f"{GOOGLE_FLIGHTS}?q={quote_plus(text)}&hl=en&curr={currency or 'USD'}"


def buy_url(offer: dict[str, Any]) -> str:
    """The provider's own booking link when it has one, otherwise a Google Flights search."""
    raw = offer.get("raw")
    if isinstance(raw, dict) and raw.get("booking_url"):
        return str(raw["booking_url"])
    return google_flights_url(offer["origin"], offer["destination"], offer["depart_date"],
                              offer.get("return_date"), offer.get("currency"))


def buy_url_is_exact(offer: dict[str, Any]) -> bool:
    raw = offer.get("raw")
    return isinstance(raw, dict) and bool(raw.get("booking_url"))


def split_clock(value: str | None) -> tuple[str, str]:
    """('2026-12-05', '07:10') for an ISO datetime; ('', '07:10') for a bare time; raw text otherwise."""
    if not value:
        return "", ""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return "", value
    return dt.date().isoformat(), dt.strftime("%H:%M")


def leg_clock(offer: dict[str, Any], index: int) -> str:
    """Departure time of leg `index` (0 = outbound, 1 = return) as shown in results."""
    legs = offer.get("legs") or []
    if len(legs) <= index:
        return ""
    return split_clock(legs[index].get("depart"))[1]


def _stops_text(stops: int | None) -> str:
    if stops is None:
        return "stops n/a"
    if stops == 0:
        return "nonstop"
    return f"{stops} stop" + ("" if stops == 1 else "s")


def _duration_text(minutes: int | None) -> str:
    if not minutes:
        return ""
    h, m = divmod(int(minutes), 60)
    return (f"{h}h " if h else "") + (f"{m}m" if m else "")


def leg_summary(leg: dict[str, Any]) -> str:
    dep_date, dep = split_clock(leg.get("depart"))
    arr_date, arr = split_clock(leg.get("arrive"))
    arrive = arr
    if arr_date and dep_date and arr_date != dep_date:
        arrive += f" (+{(datetime.fromisoformat(arr_date) - datetime.fromisoformat(dep_date)).days}d)"
    when = dep_date or ""
    parts = [
        f"{(leg.get('direction') or '').title()}: {leg.get('origin') or '?'} {dep or '?'} "
        f"→ {leg.get('destination') or '?'} {arrive or '?'}".rstrip(),
        when,
        _duration_text(leg.get("duration_minutes")),
        _stops_text(leg.get("stops")),
        ", ".join(leg.get("flights") or []) or "flight # n/a",
        "[" + (", ".join(leg.get("carriers") or []) or "airline n/a") + "]",
    ]
    return "  ".join(p for p in parts if p)


def itinerary_lines(offer: dict[str, Any]) -> list[str]:
    return [leg_summary(leg) for leg in offer.get("legs") or []]


def csv_extras(offer: dict[str, Any]) -> dict[str, str]:
    """Extra columns for the CSV export: leg times and the buy link."""
    return {
        "outbound_depart": leg_clock(offer, 0),
        "return_depart": leg_clock(offer, 1),
        "buy_url": buy_url(offer),
        "buy_url_exact": "yes" if buy_url_is_exact(offer) else "no",
    }


def trip_key(offer: dict[str, Any]) -> tuple:
    return (offer["origin"], offer["destination"], offer["depart_date"], offer.get("return_date") or "",
            offer.get("currency") or "")


def best_per_trip(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cheapest offer seen for each route and date pair, ordered by price."""
    best: dict[tuple, dict[str, Any]] = {}
    for offer in offers:
        key = trip_key(offer)
        current = best.get(key)
        if current is None or offer["price"] < current["price"]:
            best[key] = offer
    return sorted(best.values(), key=lambda o: (o["price"], o["depart_date"]))
