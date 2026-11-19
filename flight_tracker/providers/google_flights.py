"""Best-effort Google Flights scraper using a headless Chromium (Playwright).

Caveats, read before use:
  * Google Flights' terms prohibit automated access. Use a long check interval
    and keep the number of searches small.
  * The page layout is not a stable API. This parser reads the visible text of
    each result row (price, times, duration, stops, airline names) rather than
    CSS class names, which makes it more resilient, but it can still break.
  * Round-trip results show the outbound option with its total price, so the
    return leg details are not captured.
  * Install with:  pip install playwright && python -m playwright install chromium
"""
from __future__ import annotations

import re
from datetime import datetime
from ..booking import google_flights_url
from ..models import Offer, Query, utc_now_iso
from .base import Provider, ProviderError

AIRLINE_NAMES = {
    "Delta": "DL", "United": "UA", "American": "AA", "Southwest": "WN", "JetBlue": "B6",
    "Alaska": "AS", "Spirit": "NK", "Frontier": "F9", "Hawaiian": "HA", "Allegiant": "G4",
    "Air Canada": "AC", "WestJet": "WS", "Aeromexico": "AM", "Copa": "CM", "Avianca": "AV",
    "LATAM": "LA", "British Airways": "BA", "Virgin Atlantic": "VS", "Lufthansa": "LH",
    "Air France": "AF", "KLM": "KL", "Iberia": "IB", "ITA Airways": "AZ", "Swiss": "LX",
    "Austrian": "OS", "SAS": "SK", "Aer Lingus": "EI", "Ryanair": "FR", "easyJet": "U2",
    "Turkish Airlines": "TK", "Emirates": "EK", "Qatar": "QR", "Etihad": "EY",
    "Singapore Airlines": "SQ", "Cathay Pacific": "CX", "ANA": "NH", "Japan Airlines": "JL",
    "Korean Air": "KE", "Air India": "AI", "IndiGo": "6E", "Qantas": "QF", "Air New Zealand": "NZ",
}

_PRICE = re.compile(r"\$\s?([\d,]+)")
_TIME = re.compile(r"\d{1,2}:\d{2}\s?[AP]M")
_DUPLICATE_TIME = re.compile(r"(\d{1,2}:\d{2}\s?[AP]M)\1")
_DURATION = re.compile(r"(\d+)\s?hr(?:\s(\d+)\s?min)?")
_STOPS = re.compile(r"(\d+)\s+stops?", re.IGNORECASE)


def build_url(query: Query) -> str:
    return google_flights_url(query.origin, query.destination, query.depart_date.isoformat(),
                              query.return_date.isoformat() if query.return_date else None,
                              query.currency)


def parse_result_text(text: str, query: Query) -> Offer | None:
    """Turn the visible text of one result row into an Offer (None if it isn't a result)."""
    price_match = _PRICE.search(text)
    if not price_match:
        return None
    # The page repeats each time in the DOM ("1:15 PM1:15 PM"); collapse the repeats so the
    # arrival time is not taken from the departure duplicate.
    times = _TIME.findall(_DUPLICATE_TIME.sub(r"\1", text))
    duration = _DURATION.search(text)
    minutes = int(duration.group(1)) * 60 + int(duration.group(2) or 0) if duration else None
    if re.search(r"nonstop", text, re.IGNORECASE):
        stops: int | None = 0
    else:
        stops_match = _STOPS.search(text)
        stops = int(stops_match.group(1)) if stops_match else None

    carriers: list[str] = []
    for name, code in AIRLINE_NAMES.items():
        if re.search(rf"\b{re.escape(name)}\b", text) and code not in carriers:
            carriers.append(code)

    leg = {
        "direction": "outbound",
        "origin": query.origin,
        "destination": query.destination,
        "depart": times[0] if times else None,      # local airport time as shown on the page
        "arrive": times[1] if len(times) > 1 else None,
        "stops": stops,
        "duration_minutes": minutes,
        "flights": [],
        "carriers": carriers,
    }
    return Offer(
        provider="google_flights",
        vendor="Google Flights",
        origin=query.origin,
        destination=query.destination,
        depart_date=query.depart_date.isoformat(),
        return_date=query.return_date.isoformat() if query.return_date else None,
        price=float(price_match.group(1).replace(",", "")),
        currency=query.currency,
        cabin=query.cabin,
        legs=[leg],
        raw={"row_text": text[:1500], "fetched_at": datetime.now().isoformat(timespec="seconds")},
        scraped_at=utc_now_iso(),
    )


class GoogleFlightsProvider(Provider):
    key = "google_flights"
    label = "Google Flights (scraper, best effort)"

    def __init__(self, settings: dict[str, str]):
        super().__init__(settings)
        self._pw = None
        self._browser = None

    def pause_between_queries(self) -> float:
        try:
            return max(0.0, float(self.settings.get("google_delay_seconds", "8")))
        except ValueError:
            return 8.0

    def _ensure_browser(self):
        """One browser per provider instance (i.e. per run), reused across queries."""
        if self._browser is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise ProviderError("Playwright is not installed. Run: pip install playwright && "
                                    "python -m playwright install chromium") from exc
            headless = self.settings.get("google_headless", "true").lower() != "false"
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=headless)
        return self._browser

    def close(self) -> None:
        for obj, method in ((self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception:
                    pass
        self._browser = None
        self._pw = None

    def search(self, query: Query) -> list[Offer]:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        try:
            browser = self._ensure_browser()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Could not start Chromium ({exc}). Run: "
                                "python -m playwright install chromium") from exc

        page = None
        try:
            page = browser.new_page(locale="en-US")
            page.goto(build_url(query), wait_until="domcontentloaded", timeout=45000)
            # Wait for Google's own "N results returned" marker, not the first <li>
            # (navigation items exist before results do).
            page.wait_for_function(
                "() => /results returned|No results/i.test(document.body.innerText)",
                timeout=40000)
            rows = page.locator("li").all_inner_texts()
        except PlaywrightTimeout as exc:
            raise ProviderError("Google Flights results did not load in time (consent page, "
                                "block, or layout change).") from exc
        except Exception as exc:
            raise ProviderError(f"Google Flights scrape failed: {exc}") from exc
        finally:
            if page is not None:
                page.close()

        # Result cards are nested <li> elements, so the same flight appears more than once.
        offers: list[Offer] = []
        seen: set[tuple] = set()
        for row in rows:
            offer = parse_result_text(row, query)
            if offer is None:
                continue
            leg = offer.legs[0]
            key = (offer.price, leg["depart"], leg["arrive"], leg["duration_minutes"],
                   leg["stops"], tuple(leg["carriers"]))
            if key not in seen:
                seen.add(key)
                offers.append(offer)
        return offers
