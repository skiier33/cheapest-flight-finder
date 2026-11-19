"""Background scheduler: runs due trackers, stores results, and emits events for the GUI."""
from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .models import Offer, utc_now_iso
from .providers import create_provider
from .storage import Store


class TrackerService:
    """Runs trackers on their intervals in worker threads.

    Events placed on `events` (dicts with a "type" key):
      run_started: track_id, name, total, trigger
      progress:    track_id, name, done, total, offers_so_far  (after each search)
      run_done:    track_id, name, offers, min_price, currency, alerts, errors, trigger
      log:         message

    Offers are written to the store after each search rather than at the end of the
    run, so the GUI can show results while a tracker is still working.
    """

    def __init__(self, store: Store, events: queue.Queue, max_workers: int = 3):
        self.store = store
        self.events = events
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tracker")
        self._running: set[int] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def is_running(self, track_id: int) -> bool:
        with self._lock:
            return track_id in self._running

    def is_running_any(self) -> bool:
        with self._lock:
            return bool(self._running)

    def run_now(self, track_id: int) -> bool:
        """Queue an immediate run. Returns False if that tracker is already running."""
        return self._submit(track_id, "manual")

    # ---- internals ---------------------------------------------------------
    def _submit(self, track_id: int, trigger: str) -> bool:
        with self._lock:
            if track_id in self._running:
                return False
            self._running.add(track_id)
        self._executor.submit(self._run_guarded, track_id, trigger)
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:  # keep the scheduler alive no matter what
                self.events.put({"type": "log", "message": f"Scheduler error: {exc}"})
            self._stop.wait(15)

    def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        for track in self.store.list_tracks():
            if not track["active"]:
                continue
            last = track["last_run_at"]
            due = last is None or now >= datetime.fromisoformat(last) + timedelta(
                minutes=track["interval_minutes"])
            if due:
                self._submit(track["id"], "scheduled")

    def _report(self, track_id: int, name: str, done: int, total: int, offers_so_far: int) -> None:
        self.events.put({"type": "progress", "track_id": track_id, "name": name,
                         "done": done, "total": total, "offers_so_far": offers_so_far})

    def _run_guarded(self, track_id: int, trigger: str) -> None:
        try:
            self._execute(track_id, trigger)
        except Exception as exc:
            self.events.put({"type": "log", "message": f"Tracker {track_id} failed: {exc}"})
        finally:
            with self._lock:
                self._running.discard(track_id)

    def _execute(self, track_id: int, trigger: str) -> None:
        track = self.store.get_track(track_id)
        if track is None:
            return
        spec = track["spec"]
        self.store.touch_track(track_id, utc_now_iso())  # set before searching so failures don't spin
        run_id = self.store.start_run(track_id, trigger)
        previous_low = self.store.min_price(track_id, before_run_id=run_id)
        settings = self.store.effective_settings()

        queries = spec.queries()
        total = len(queries) * len(spec.providers)
        offers: list[Offer] = []
        errors: list[str] = []
        done = 0
        self.events.put({"type": "run_started", "track_id": track_id, "name": spec.name,
                         "total": total, "trigger": trigger})
        for key in spec.providers:
            provider = create_provider(key, settings)
            try:
                missing = provider.missing_config()
                if missing:
                    errors.append(f"{key}: missing settings ({', '.join(missing)})")
                    done += len(queries)
                    self._report(track_id, spec.name, done, total, len(offers))
                    continue
                for query in queries:
                    if self._stop.is_set():
                        break
                    kept: list[Offer] = []
                    try:
                        found = provider.search(query)
                        kept = [o for o in found if spec.allows(o)]
                    except Exception as exc:
                        errors.append(f"{key} {query.origin}->{query.destination} "
                                      f"{query.depart_date}: {exc}")
                    if kept:
                        self.store.save_offers(track_id, run_id, kept)
                        offers.extend(kept)
                    done += 1
                    self._report(track_id, spec.name, done, total, len(offers))
                    pause = provider.pause_between_queries()
                    if pause:
                        time.sleep(pause)
            finally:
                provider.close()

        status = "ok" if not errors else ("error" if not offers else "partial")
        self.store.finish_run(run_id, status, done, len(offers), errors)

        min_price = min((o.price for o in offers), default=None)
        alerts: list[str] = []
        if min_price is not None and spec.budget is not None and min_price <= spec.budget:
            alerts.append(f"Under budget: {spec.currency} {min_price:,.2f} "
                          f"(budget {spec.budget:,.2f})")
        if min_price is not None and previous_low is not None and min_price < previous_low:
            alerts.append(f"New low for '{spec.name}': {spec.currency} {min_price:,.2f} "
                          f"(was {previous_low:,.2f})")

        self.events.put({
            "type": "run_done", "track_id": track_id, "name": spec.name,
            "offers": len(offers), "min_price": min_price, "currency": spec.currency,
            "alerts": alerts, "errors": errors, "trigger": trigger,
        })
