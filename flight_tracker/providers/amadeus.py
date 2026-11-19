"""Amadeus Self-Service Flight Offers Search API (official, free test tier).

Sign up at https://developers.amadeus.com, create an app, and copy the API key
(client ID) and secret into Settings. The test environment returns sample data;
the production environment needs an approved production key.
"""
from __future__ import annotations

import re
import time
from typing import Any

import requests

from ..models import Offer, Query, utc_now_iso
from .base import Provider, ProviderError

_DURATION = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?")


def parse_iso_duration(value: str | None) -> int | None:
    """'PT2H30M' -> 150."""
    match = _DURATION.fullmatch(value or "")
    if not match:
        return None
    return int(match.group(1) or 0) * 60 + int(match.group(2) or 0)


def parse_offer(item: dict[str, Any], query: Query) -> Offer:
    """Convert one Amadeus flight-offer JSON object into an Offer."""
    legs: list[dict[str, Any]] = []
    for index, itinerary in enumerate(item.get("itineraries", [])):
        segments = itinerary["segments"]
        first, last = segments[0], segments[-1]
        legs.append({
            "direction": "outbound" if index == 0 else "return",
            "origin": first["departure"]["iataCode"],
            "destination": last["arrival"]["iataCode"],
            "depart": first["departure"]["at"],
            "arrive": last["arrival"]["at"],
            "stops": len(segments) - 1,
            "duration_minutes": parse_iso_duration(itinerary.get("duration")),
            "flights": [f'{s["carrierCode"]}{s["number"]}' for s in segments],
            "carriers": sorted({s["carrierCode"] for s in segments}),
        })

    validating = item.get("validatingAirlineCodes", [])
    vendor = "Amadeus GDS"
    if validating:
        vendor += f" (ticketed by {', '.join(validating)})"

    price = item["price"]
    seats = item.get("numberOfBookableSeats")
    return Offer(
        provider="amadeus",
        vendor=vendor,
        origin=query.origin,
        destination=query.destination,
        depart_date=query.depart_date.isoformat(),
        return_date=query.return_date.isoformat() if query.return_date else None,
        price=float(price["grandTotal"]),
        currency=price.get("currency", query.currency),
        cabin=query.cabin,
        legs=legs,
        seats_left=seats,
        raw={"id": item.get("id"), "validatingAirlineCodes": validating,
             "lastTicketingDate": item.get("lastTicketingDate")},
        scraped_at=utc_now_iso(),
    )


class AmadeusProvider(Provider):
    key = "amadeus"
    label = "Amadeus API (official, free key)"

    def __init__(self, settings: dict[str, str]):
        super().__init__(settings)
        production = settings.get("amadeus_env") == "production"
        self.base = "https://api.amadeus.com" if production else "https://test.api.amadeus.com"
        self._session = requests.Session()
        self._token: str | None = None
        self._token_expires_at = 0.0

    def missing_config(self) -> list[str]:
        return [k for k in ("amadeus_client_id", "amadeus_client_secret")
                if not self.settings.get(k)]

    @staticmethod
    def _error_text(response: requests.Response) -> str:
        try:
            errors = response.json().get("errors", [])
            if errors:
                return errors[0].get("detail") or errors[0].get("title") or response.text[:200]
        except ValueError:
            pass
        return response.text[:200]

    def _get_token(self, force: bool = False) -> str:
        if self._token and not force and time.time() < self._token_expires_at - 60:
            return self._token
        response = self._session.post(
            f"{self.base}/v1/security/oauth2/token",
            data={"grant_type": "client_credentials",
                  "client_id": self.settings["amadeus_client_id"],
                  "client_secret": self.settings["amadeus_client_secret"]},
            timeout=30)
        if response.status_code != 200:
            raise ProviderError(f"Amadeus authentication failed ({response.status_code}): "
                                f"{self._error_text(response)}")
        body = response.json()
        self._token = body["access_token"]
        self._token_expires_at = time.time() + int(body.get("expires_in", 1799))
        return self._token

    def search(self, query: Query) -> list[Offer]:
        params: dict[str, Any] = {
            "originLocationCode": query.origin,
            "destinationLocationCode": query.destination,
            "departureDate": query.depart_date.isoformat(),
            "adults": query.adults,
            "travelClass": query.cabin,
            "currencyCode": query.currency,
            "max": 250,
        }
        if query.return_date:
            params["returnDate"] = query.return_date.isoformat()
        if query.children:
            params["children"] = query.children
        if query.max_stops == 0:
            params["nonStop"] = "true"

        for attempt in (0, 1):
            token = self._get_token(force=attempt == 1)
            response = self._session.get(
                f"{self.base}/v2/shopping/flight-offers", params=params,
                headers={"Authorization": f"Bearer {token}"}, timeout=60)
            if response.status_code == 401 and attempt == 0:
                continue  # token expired; retry once with a fresh token
            break

        if response.status_code == 429:
            raise ProviderError("Amadeus rate limit reached (429). Use a longer check interval.")
        if response.status_code != 200:
            raise ProviderError(f"Amadeus search failed ({response.status_code}): "
                                f"{self._error_text(response)}")
        return [parse_offer(item, query) for item in response.json().get("data", [])]
