"""Airport lookup: accepts IATA codes (JFK), ICAO codes (KJFK), and city names.

The built-in table covers major airports only. Any unknown three-letter code is
accepted as an IATA code, so smaller airports still work when typed directly.
"""
from __future__ import annotations

import re

# (IATA, ICAO, city)
AIRPORTS: list[tuple[str, str, str]] = [
    ("JFK", "KJFK", "New York"), ("LGA", "KLGA", "New York"), ("EWR", "KEWR", "New York"),
    ("LAX", "KLAX", "Los Angeles"), ("SFO", "KSFO", "San Francisco"), ("OAK", "KOAK", "Oakland"),
    ("SJC", "KSJC", "San Jose"), ("SAN", "KSAN", "San Diego"), ("SNA", "KSNA", "Orange County"),
    ("ORD", "KORD", "Chicago"), ("MDW", "KMDW", "Chicago"),
    ("DFW", "KDFW", "Dallas"), ("DAL", "KDAL", "Dallas"), ("IAH", "KIAH", "Houston"), ("HOU", "KHOU", "Houston"),
    ("AUS", "KAUS", "Austin"), ("SAT", "KSAT", "San Antonio"),
    ("ATL", "KATL", "Atlanta"), ("MIA", "KMIA", "Miami"), ("FLL", "KFLL", "Fort Lauderdale"),
    ("MCO", "KMCO", "Orlando"), ("TPA", "KTPA", "Tampa"), ("RSW", "KRSW", "Fort Myers"),
    ("BOS", "KBOS", "Boston"), ("DCA", "KDCA", "Washington"), ("IAD", "KIAD", "Washington"),
    ("BWI", "KBWI", "Baltimore"), ("PHL", "KPHL", "Philadelphia"), ("PIT", "KPIT", "Pittsburgh"),
    ("DEN", "KDEN", "Denver"), ("PHX", "KPHX", "Phoenix"), ("LAS", "KLAS", "Las Vegas"),
    ("SEA", "KSEA", "Seattle"), ("PDX", "KPDX", "Portland"), ("SLC", "KSLC", "Salt Lake City"),
    ("MSP", "KMSP", "Minneapolis"), ("DTW", "KDTW", "Detroit"), ("CLT", "KCLT", "Charlotte"),
    ("MSY", "KMSY", "New Orleans"), ("STL", "KSTL", "St. Louis"), ("MCI", "KMCI", "Kansas City"),
    ("CLE", "KCLE", "Cleveland"), ("CVG", "KCVG", "Cincinnati"), ("RDU", "KRDU", "Raleigh"),
    ("BNA", "KBNA", "Nashville"), ("MEM", "KMEM", "Memphis"), ("IND", "KIND", "Indianapolis"),
    ("HNL", "PHNL", "Honolulu"), ("OGG", "PHOG", "Maui"), ("ANC", "PANC", "Anchorage"),
    ("YYZ", "CYYZ", "Toronto"), ("YVR", "CYVR", "Vancouver"), ("YUL", "CYUL", "Montreal"),
    ("MEX", "MMMX", "Mexico City"), ("CUN", "MMUN", "Cancun"), ("GDL", "MMGL", "Guadalajara"),
    ("LHR", "EGLL", "London"), ("LGW", "EGKK", "London"), ("STN", "EGSS", "London"), ("LTN", "EGGW", "London"),
    ("CDG", "LFPG", "Paris"), ("ORY", "LFPO", "Paris"), ("FRA", "EDDF", "Frankfurt"),
    ("MUC", "EDDM", "Munich"), ("AMS", "EHAM", "Amsterdam"), ("BCN", "LEBL", "Barcelona"),
    ("MAD", "LEMD", "Madrid"), ("FCO", "LIRF", "Rome"), ("MXP", "LIMC", "Milan"),
    ("ZRH", "LSZH", "Zurich"), ("VIE", "LOWW", "Vienna"), ("CPH", "EKCH", "Copenhagen"),
    ("ARN", "ESSA", "Stockholm"), ("DUB", "EIDW", "Dublin"), ("LIS", "LPPT", "Lisbon"),
    ("ATH", "LGAV", "Athens"), ("IST", "LTFM", "Istanbul"), ("KEF", "BIKF", "Reykjavik"),
    ("DXB", "OMDB", "Dubai"), ("DOH", "OTHH", "Doha"), ("DEL", "VIDP", "Delhi"),
    ("BOM", "VABB", "Mumbai"), ("HND", "RJTT", "Tokyo"), ("NRT", "RJAA", "Tokyo"),
    ("ICN", "RKSI", "Seoul"), ("PEK", "ZBAA", "Beijing"), ("PVG", "ZSPD", "Shanghai"),
    ("HKG", "VHHH", "Hong Kong"), ("SIN", "WSSS", "Singapore"), ("BKK", "VTBS", "Bangkok"),
    ("SYD", "YSSY", "Sydney"), ("MEL", "YMML", "Melbourne"), ("AKL", "NZAA", "Auckland"),
    ("GRU", "SBGR", "Sao Paulo"), ("EZE", "SAEZ", "Buenos Aires"), ("BOG", "SKBO", "Bogota"),
    ("LIM", "SPJC", "Lima"), ("SCL", "SCEL", "Santiago"), ("JNB", "FAOR", "Johannesburg"),
    ("CPT", "FACT", "Cape Town"), ("CAI", "HECA", "Cairo"), ("ADD", "HAAB", "Addis Ababa"),
]

# Metro / nickname aliases that map to a city name in AIRPORTS.
CITY_ALIASES = {
    "nyc": "new york", "la": "los angeles", "sf": "san francisco", "dc": "washington",
    "chi": "chicago", "nola": "new orleans", "vegas": "las vegas", "london uk": "london",
    "philly": "philadelphia", "slc": "salt lake city", "sd": "san diego",
}

BY_IATA = {iata: (icao, city) for iata, icao, city in AIRPORTS}
BY_ICAO = {icao: iata for iata, icao, _ in AIRPORTS}

_IATA_RE = re.compile(r"[A-Z]{3}")


def resolve_token(token: str) -> list[str]:
    """Resolve one user-entered token to one or more IATA codes."""
    raw = token.strip()
    if not raw:
        return []
    upper = raw.upper()
    if upper in BY_IATA:
        return [upper]
    if upper in BY_ICAO:
        return [BY_ICAO[upper]]
    city = CITY_ALIASES.get(raw.lower(), raw).lower()
    matches = sorted({iata for iata, _, name in AIRPORTS
                      if name.lower() == city or city in name.lower()})
    if matches:
        return matches
    if _IATA_RE.fullmatch(upper):
        return [upper]  # not in the table, but a valid-looking IATA code
    raise ValueError(f"Can't find an airport or city named '{raw}'.")


def resolve_airports(text: str) -> list[str]:
    """Resolve a comma-separated list such as 'JFK, New York, KLAX' to unique IATA codes."""
    codes: list[str] = []
    for token in text.split(","):
        for code in resolve_token(token):
            if code not in codes:
                codes.append(code)
    if not codes:
        raise ValueError("Enter at least one airport code or city.")
    return codes


def describe(code: str) -> str:
    if code in BY_IATA:
        icao, city = BY_IATA[code]
        return f"{code} ({icao}) {city}"
    return code
