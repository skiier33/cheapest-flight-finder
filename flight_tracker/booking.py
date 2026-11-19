"""Buy links, itinerary summaries, and best-per-trip selection for stored offers.

Offers are plain dicts as returned by Store.latest_offers (with `legs` and `raw` decoded).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

from .models import COMBO_PROVIDER

GOOGLE_FLIGHTS = "https://www.google.com/travel/flights"


def google_flights_url(origin: str, destination: str, depart_date: str,
                       return_date: str | None, currency: str | None) -> str:
    """Google Flights search for one route and date pair (not a fixed fare)."""
    text = f"Flights to {destination} from {origin} on {depart_date}"
    text += f" through {return_date}" if return_date else " one way"
    return f"{GOOGLE_FLIGHTS}?q={quote_plus(text)}&hl=en&curr={currency or 'USD'}"


def is_combo(offer: dict[str, Any]) -> bool:
    return offer.get("provider") == COMBO_PROVIDER


def buy_links(offer: dict[str, Any]) -> list[tuple[str, str, bool]]:
    """(label, url, exact) for each ticket to buy. A combo has two (outbound and return)."""
    raw = offer.get("raw") if isinstance(offer.get("raw"), dict) else {}
    if is_combo(offer):
        out = raw.get("outbound") or {}
        back = raw.get("return") or {}
        return [
            ("Outbound ticket", out.get("booking_url") or google_flights_url(
                offer["origin"], offer["destination"], offer["depart_date"], None,
                offer.get("currency")), bool(out.get("booking_url"))),
            ("Return ticket", back.get("booking_url") or google_flights_url(
                offer["destination"], offer["origin"], offer["return_date"], None,
                offer.get("currency")), bool(back.get("booking_url"))),
        ]
    if raw.get("booking_url"):
        return [("", str(raw["booking_url"]), True)]
    return [("", google_flights_url(offer["origin"], offer["destination"], offer["depart_date"],
                                    offer.get("return_date"), offer.get("currency")), False)]


def buy_url(offer: dict[str, Any]) -> str:
    """The provider's own booking link when it has one, otherwise a Google Flights search.
    For a two-ticket combo this is the outbound ticket; see buy_links for both."""
    return buy_links(offer)[0][1]


def buy_url_is_exact(offer: dict[str, Any]) -> bool:
    return all(exact for _label, _url, exact in buy_links(offer))


def combo_savings(offer: dict[str, Any]) -> float | None:
    """How much two one-way tickets save against the round-trip fare (negative = costlier)."""
    if not is_combo(offer):
        return None
    raw = offer.get("raw") if isinstance(offer.get("raw"), dict) else {}
    return raw.get("savings")


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
    """Extra columns for the CSV export: leg times, buy links and the one-way comparison."""
    links = buy_links(offer)
    savings = combo_savings(offer)
    return {
        "outbound_depart": leg_clock(offer, 0),
        "return_depart": leg_clock(offer, 1),
        "buy_url": links[0][1],
        "buy_url_exact": "yes" if buy_url_is_exact(offer) else "no",
        "return_buy_url": links[1][1] if is_combo(offer) else "",
        "saving_vs_round_trip": "" if savings is None else f"{savings:.2f}",
    }


def trip_key(offer: dict[str, Any]) -> tuple:
    return (offer["origin"], offer["destination"], offer["depart_date"], offer.get("return_date") or "",
            offer.get("currency") or "", is_combo(offer))


def best_per_trip(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cheapest offer seen for each route and date pair, ordered by price.

    Two-ticket combinations are kept apart from round-trip fares so each can be compared
    with the other."""
    best: dict[tuple, dict[str, Any]] = {}
    for offer in offers:
        key = trip_key(offer)
        current = best.get(key)
        if current is None or offer["price"] < current["price"]:
            best[key] = offer
    return sorted(best.values(), key=lambda o: (o["price"], o["depart_date"]))
