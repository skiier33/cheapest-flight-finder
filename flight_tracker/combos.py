"""Price a round trip as two separate one-way tickets and compare it with the round-trip fare.

For each round-trip (route and date pair) the cheapest outbound one-way and the cheapest
return one-way are combined, even when they come from different sources or airlines. The
result is an Offer with provider COMBO_PROVIDER whose `raw` records both tickets and how
much it saves against the cheapest round-trip offer found in the same run.
"""
from __future__ import annotations

from typing import Any, Iterable

from .models import COMBO_PROVIDER, Offer, Query, utc_now_iso


def _cheapest_one_way(offers: Iterable[Offer]) -> dict[tuple[str, str, str], Offer]:
    """Cheapest one-way offer per (origin, destination, depart date ISO string)."""
    best: dict[tuple[str, str, str], Offer] = {}
    for offer in offers:
        key = (offer.origin, offer.destination, offer.depart_date)
        current = best.get(key)
        if current is None or offer.price < current.price:
            best[key] = offer
    return best


def _component(offer: Offer) -> dict[str, Any]:
    raw = offer.raw if isinstance(offer.raw, dict) else {}
    return {
        "provider": offer.provider,
        "vendor": offer.vendor,
        "price": offer.price,
        "booking_url": raw.get("booking_url"),
    }


def build_one_way_combos(round_trips: Iterable[Query], round_trip_offers: Iterable[Offer],
                         leg_offers: Iterable[Offer]) -> list[Offer]:
    """Build one combined offer per round-trip query that has both one-way legs priced.

    `round_trips` are the round-trip queries that were searched; `round_trip_offers` are the
    round-trip offers found for them; `leg_offers` are the one-way offers (return_date None).
    Offers already filtered by the tracker's stop and airline rules should be passed in.
    """
    legs = _cheapest_one_way(leg_offers)
    rt_best: dict[tuple[str, str, str, str], Offer] = {}
    for offer in round_trip_offers:
        if offer.provider == COMBO_PROVIDER or not offer.return_date:
            continue
        key = (offer.origin, offer.destination, offer.depart_date, offer.return_date)
        current = rt_best.get(key)
        if current is None or offer.price < current.price:
            rt_best[key] = offer

    combos: list[Offer] = []
    seen: set[tuple[str, str, str, str]] = set()
    for trip in round_trips:
        if trip.return_date is None:
            continue
        trip_key = (trip.origin, trip.destination, trip.depart_date.isoformat(),
                    trip.return_date.isoformat())
        if trip_key in seen:
            continue
        seen.add(trip_key)

        outbound = legs.get((trip.origin, trip.destination, trip.depart_date.isoformat()))
        inbound = legs.get((trip.destination, trip.origin, trip.return_date.isoformat()))
        if outbound is None or inbound is None:
            continue

        rt = rt_best.get(trip_key)
        price = round(outbound.price + inbound.price, 2)
        savings = round(rt.price - price, 2) if rt is not None else None

        return_leg = dict(inbound.legs[0]) if inbound.legs else {}
        return_leg["direction"] = "return"
        legs_out = [dict(leg) for leg in outbound.legs[:1]] + [return_leg]

        combos.append(Offer(
            provider=COMBO_PROVIDER,
            vendor="Two one-way tickets",
            origin=trip.origin,
            destination=trip.destination,
            depart_date=trip.depart_date.isoformat(),
            return_date=trip.return_date.isoformat(),
            price=price,
            currency=outbound.currency,
            cabin=trip.cabin,
            legs=legs_out,
            seats_left=min((s for s in (outbound.seats_left, inbound.seats_left) if s is not None),
                           default=None),
            raw={
                "kind": COMBO_PROVIDER,
                "outbound": _component(outbound),
                "return": _component(inbound),
                "round_trip_best": rt.price if rt is not None else None,
                "round_trip_provider": rt.provider if rt is not None else None,
                "savings": savings,
            },
            scraped_at=utc_now_iso(),
        ))
    return combos
