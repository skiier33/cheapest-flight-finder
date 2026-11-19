"""SQLite persistence: trackers, run history, and every scraped offer with metadata."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import COMBO_PROVIDER, Offer, SearchSpec, utc_now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS tracks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    spec_json        TEXT NOT NULL,
    active           INTEGER NOT NULL DEFAULT 1,
    interval_minutes INTEGER NOT NULL,
    created_at       TEXT NOT NULL,
    last_run_at      TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id     INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    trigger      TEXT NOT NULL,
    status       TEXT NOT NULL,
    query_count  INTEGER NOT NULL DEFAULT 0,
    offer_count  INTEGER NOT NULL DEFAULT 0,
    errors       TEXT
);
CREATE TABLE IF NOT EXISTS offers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id         INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    run_id           INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    scraped_at       TEXT NOT NULL,
    provider         TEXT,
    vendor           TEXT,
    origin           TEXT,
    destination      TEXT,
    depart_date      TEXT,
    return_date      TEXT,
    price            REAL NOT NULL,
    currency         TEXT,
    airlines         TEXT,
    flight_numbers   TEXT,
    outbound_stops   INTEGER,
    return_stops     INTEGER,
    duration_minutes INTEGER,
    cabin            TEXT,
    seats_left       INTEGER,
    legs_json        TEXT,
    raw_json         TEXT
);
CREATE INDEX IF NOT EXISTS idx_offers_track ON offers(track_id, scraped_at);
CREATE INDEX IF NOT EXISTS idx_offers_run ON offers(run_id);
"""

DEFAULT_SETTINGS = {
    "amadeus_env": "test",              # "test" or "production"
    "google_headless": "true",
    "google_delay_seconds": "8",
}
ENV_FALLBACKS = {
    "amadeus_client_id": "AMADEUS_CLIENT_ID",
    "amadeus_client_secret": "AMADEUS_CLIENT_SECRET",
}


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- low-level helpers -------------------------------------------------
    def _execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _executemany(self, sql: str, rows: list[tuple]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)
            self._conn.commit()

    def _fetch(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # ---- settings ----------------------------------------------------------
    def get_settings(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self._fetch("SELECT key, value FROM settings")}

    def set_settings(self, values: dict[str, str]) -> None:
        self._executemany("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                          [(k, v) for k, v in values.items()])

    def effective_settings(self) -> dict[str, str]:
        """Stored settings, falling back to environment variables and defaults."""
        merged = dict(DEFAULT_SETTINGS)
        merged.update({k: v for k, v in self.get_settings().items() if v})
        for key, env_name in ENV_FALLBACKS.items():
            if not merged.get(key) and os.environ.get(env_name):
                merged[key] = os.environ[env_name]
        return merged

    # ---- trackers ----------------------------------------------------------
    def add_track(self, name: str, spec: SearchSpec) -> int:
        cur = self._execute(
            "INSERT INTO tracks(name, spec_json, active, interval_minutes, created_at) "
            "VALUES (?, ?, 1, ?, ?)",
            (name, spec.to_json(), spec.interval_minutes, utc_now_iso()))
        return int(cur.lastrowid)

    def update_track(self, track_id: int, name: str, spec: SearchSpec) -> None:
        self._execute("UPDATE tracks SET name = ?, spec_json = ?, interval_minutes = ? WHERE id = ?",
                      (name, spec.to_json(), spec.interval_minutes, track_id))

    def set_track_active(self, track_id: int, active: bool) -> None:
        self._execute("UPDATE tracks SET active = ? WHERE id = ?", (1 if active else 0, track_id))

    def touch_track(self, track_id: int, when_iso: str) -> None:
        self._execute("UPDATE tracks SET last_run_at = ? WHERE id = ?", (when_iso, track_id))

    def delete_track(self, track_id: int) -> None:
        self._execute("DELETE FROM tracks WHERE id = ?", (track_id,))

    def _row_to_track(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["spec"] = SearchSpec.from_json(row.pop("spec_json"))
        row["active"] = bool(row["active"])
        return row

    def list_tracks(self) -> list[dict[str, Any]]:
        return [self._row_to_track(r) for r in self._fetch("SELECT * FROM tracks ORDER BY id")]

    def get_track(self, track_id: int) -> dict[str, Any] | None:
        rows = self._fetch("SELECT * FROM tracks WHERE id = ?", (track_id,))
        return self._row_to_track(rows[0]) if rows else None

    # ---- runs --------------------------------------------------------------
    def start_run(self, track_id: int, trigger: str) -> int:
        cur = self._execute(
            "INSERT INTO runs(track_id, started_at, trigger, status) VALUES (?, ?, ?, 'running')",
            (track_id, utc_now_iso(), trigger))
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, query_count: int,
                   offer_count: int, errors: list[str]) -> None:
        self._execute(
            "UPDATE runs SET finished_at = ?, status = ?, query_count = ?, offer_count = ?, errors = ? "
            "WHERE id = ?",
            (utc_now_iso(), status, query_count, offer_count, "\n".join(errors) or None, run_id))

    def close_stale_runs(self) -> int:
        """Runs still marked 'running' at startup were cut off by a previous app exit."""
        cur = self._execute(
            "UPDATE runs SET status = 'interrupted', finished_at = ? WHERE status = 'running'",
            (utc_now_iso(),))
        return cur.rowcount

    def list_runs(self, track_id: int, limit: int = 50) -> list[dict[str, Any]]:
        return self._fetch("SELECT * FROM runs WHERE track_id = ? ORDER BY id DESC LIMIT ?",
                           (track_id, limit))

    # ---- offers ------------------------------------------------------------
    def save_offers(self, track_id: int, run_id: int, offers: list[Offer]) -> None:
        rows = []
        for o in offers:
            rows.append((
                track_id, run_id, o.scraped_at or utc_now_iso(), o.provider, o.vendor,
                o.origin, o.destination, o.depart_date, o.return_date, o.price, o.currency,
                ",".join(o.airlines), ",".join(o.flight_numbers),
                o.outbound_stops, o.return_stops, o.duration_minutes, o.cabin, o.seats_left,
                json.dumps(o.legs), json.dumps(o.raw, default=str),
            ))
        if rows:
            self._executemany(
                "INSERT INTO offers(track_id, run_id, scraped_at, provider, vendor, origin, destination, "
                "depart_date, return_date, price, currency, airlines, flight_numbers, outbound_stops, "
                "return_stops, duration_minutes, cabin, seats_left, legs_json, raw_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    def latest_offers(self, track_id: int, limit: int | None = 500) -> list[dict[str, Any]]:
        sql = "SELECT * FROM offers WHERE track_id = ? ORDER BY scraped_at DESC, price ASC"
        params: tuple = (track_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = (track_id, limit)
        out = []
        for row in self._fetch(sql, params):
            row["legs"] = json.loads(row.pop("legs_json") or "[]")
            row["raw"] = json.loads(row.pop("raw_json") or "null")
            out.append(row)
        return out

    def min_price(self, track_id: int, before_run_id: int | None = None) -> float | None:
        """Lowest round-trip (or one-way) price. Two-ticket combinations are excluded."""
        sql = "SELECT MIN(price) AS p FROM offers WHERE track_id = ? AND provider != ?"
        params: list = [track_id, COMBO_PROVIDER]
        if before_run_id is not None:
            sql += " AND run_id < ?"
            params.append(before_run_id)
        rows = self._fetch(sql, params)
        return rows[0]["p"] if rows and rows[0]["p"] is not None else None

    def price_history(self, track_id: int) -> list[tuple[str, float]]:
        """(run start time, lowest round-trip price found in that run) for each run with results."""
        rows = self._fetch(
            "SELECT r.started_at AS t, MIN(o.price) AS p FROM runs r "
            "JOIN offers o ON o.run_id = r.id WHERE r.track_id = ? AND o.provider != ? "
            "GROUP BY r.id ORDER BY r.started_at", (track_id, COMBO_PROVIDER))
        return [(r["t"], r["p"]) for r in rows]
