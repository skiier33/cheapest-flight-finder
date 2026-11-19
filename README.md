# Flight Price Tracker

A desktop app (Tkinter) that tracks airfares on a schedule you choose and stores every price it finds, with timestamps, vendor, airline, flight numbers, stops, duration, and the raw source data.

## Quick start

```powershell
pip install -r requirements.txt
python run_tracker.py
```

No account is needed to try it. The **Demo** price source is selected by default and produces synthetic prices. To get real prices, add a source (see below).

Run the tests:

```powershell
python -m unittest discover -s tests -v
```

## Creating a tracker (Search Setup tab)

| Field | Notes |
|---|---|
| Origins / destinations | Comma-separated. Accepts IATA (`JFK`), ICAO (`KJFK`), or city names (`New York`, `nyc`, `Tokyo`). Metro cities expand to all their airports. Any 3-letter code not in the built-in table is accepted as an IATA code. |
| Strict dates | Exact depart date. Leave return blank for one-way. |
| Flexible range | Depart window (from/to) × trip length (min/max nights). Leave the nights blank for one-way. |
| Passengers | Adults and children. |
| Cabin | Economy, premium economy, business, first. |
| Max stops | Any, nonstop, ≤1, ≤2. Applied to every leg. |
| Airlines | Include-only and exclude lists using 2-letter carrier codes. |
| Budget | Triggers a price alert when a run finds a fare at or below this amount. |
| Currency | Requested currency for prices. |
| Check every | Minutes between automatic runs (minimum 5). |
| Max dates per route | Caps how many date combinations a flexible range expands to, which limits API calls and scraping volume. When the cap is hit, dates are sampled evenly across the range. |
| Price sources | Demo, Amadeus API, Google Flights scraper. Select one or more. |

Trackers run automatically in the background while the app is open. A tracker runs immediately when first saved, then on its interval. Runs survive app restarts, and the schedule resumes from the last run time.

While a tracker runs, the status bar shows a progress bar and the Trackers tab shows a live count (for example `◐ 4/12 · 9 offers`). **Save & run now** (or Ctrl+Enter on the setup tab) opens the Results tab on that tracker, and each offer is added to the table as soon as its search finishes. New rows flash yellow, and the cheapest row is shaded green. Price alerts appear as a notification in the bottom-right corner. Click it to open that tracker's results. F5 refreshes the current tab.

## Price sources

| Source | How it works | Setup |
|---|---|---|
| **Amadeus API** (recommended) | Official Flight Offers Search API. Returns GDS fares with ticketing airline and seat counts. | Free key at developers.amadeus.com. Enter it in *Settings*, or set `AMADEUS_CLIENT_ID` and `AMADEUS_CLIENT_SECRET`. The test environment returns sample data, and production needs an approved key. |
| **Google Flights scraper** | Opens Google Flights in headless Chromium and parses the visible result text. | `python -m playwright install chromium`. Best effort. See the caveats below. |
| **Demo** | Synthetic, deterministic prices for testing the pipeline. | None. |

### Google Flights caveats

- Google's terms prohibit automated access. Use long check intervals and a small number of searches, and accept the risk.
- The page is not a stable API. The parser reads the text of each row, not CSS class names, which helps, but layout or wording changes can still break it. Failures appear in the run history.
- Round-trip rows show the outbound option with a total price, so return-leg details are not captured.
- Consent pages (some regions) can block results. Run the browser non-headless (Settings → `google_headless = false`) to click through them once.

### Other sites

Most airline and aggregator sites (Kayak, Skyscanner, Expedia, and airline sites) either block scraping or forbid it in their terms. To add a source, subclass `flight_tracker.providers.base.Provider`, implement `search(query) -> list[Offer]`, and register it in `flight_tracker/providers/__init__.py`.

## What gets stored

Each run is recorded in the `runs` table (start/finish time, trigger manual or scheduled, status, query count, offer count, errors). Each offer is in the `offers` table:

- `scraped_at` (UTC), `provider`, `vendor` (who sells or ticketed the fare)
- `origin`, `destination`, `depart_date`, `return_date`, `cabin`
- `price`, `currency`, `seats_left`
- `airlines`, `flight_numbers`, `outbound_stops`, `return_stops`, `duration_minutes`
- `legs_json` (per-leg times, stops, carriers) and `raw_json` (the source payload or scraped row text)

The database is `flight_tracker.db` in the project folder. Override it with the `FLIGHT_TRACKER_DB` environment variable. Use **Results → Export CSV** to take the data elsewhere.

### Choosing and buying a fare

The Results tab sorts by price by default and, with **Best price per trip** ticked, shows the cheapest offer seen for each route and date pair across all scrapes. Each row shows depart and return dates, outbound and return departure times, airlines, flight numbers (when the source provides them), stops, duration, vendor, and when it was last seen. Click a column heading to sort by it.

Select a row to see the full itinerary (times, durations, stops, carriers), then use **Open buy page** (or double-click the row) or **Copy link**. The *Buy link* column says `fare` when the provider supplied its own booking link, and `search` when the link is a Google Flights search for those dates. A search link does not guarantee that price, so confirm the fare before buying.

Export CSV includes `outbound_depart`, `return_depart`, `buy_url`, and `buy_url_exact`. In the chart page, clicking a dot opens its buy link.

Stored rows scraped before this change keep the arrival times they were saved with. Google Flights arrival times were wrong (they repeated the departure time) until the parser fix, so re-run a tracker to refresh them.

### Charting a CSV

**Results → Chart CSV...** (or `python -m flight_tracker.visualize prices.csv`) writes a self-contained HTML page next to the CSV and opens it in your browser. It plots price over time with one series per flight number, airline, vendor, outbound stops, route, or depart date. Legend checkboxes hide series, a currency selector appears when the file mixes currencies, and **Show table** lists the filtered offers. Flight numbers come from the Amadeus and Demo sources only. The Google Flights scraper does not capture them, so that data shows `(unknown)` in the flight-number grouping.

## Project layout

```
run_tracker.py              launcher
flight_tracker/
  gui.py                    Tkinter UI (Search Setup, Trackers, Results, Settings)
  scheduler.py              background runs, intervals, price alerts
  storage.py                SQLite schema and queries
  models.py                 SearchSpec (dates, filters), Query, Offer
  airports.py               IATA / ICAO / city resolution
  providers/                amadeus.py, google_flights.py, demo.py, base.py
tests/test_core.py          unit tests (no network needed)
```

## Notes

- Secrets saved in *Settings* are stored in plain text in the local database. Environment variables are the safer option.
- Keep the app open for scheduled runs. Trackers do not run while it is closed.
