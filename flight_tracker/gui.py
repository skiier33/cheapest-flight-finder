"""Tkinter desktop GUI for the flight price tracker."""
from __future__ import annotations

import csv
import os
import queue
import tkinter as tk
import webbrowser
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .airports import describe, resolve_airports
from .models import CABINS, SearchSpec
from .providers import REGISTRY, create_provider
from .scheduler import TrackerService
from .booking import (best_per_trip, buy_url, buy_url_is_exact, csv_extras, itinerary_lines,
                      leg_clock)
from .storage import Store
from .visualize import write_page

SPINNER = "◐◓◑◒"
STOP_CHOICES = {"Any": None, "Nonstop only": 0, "Max 1 stop": 1, "Max 2 stops": 2}
CURRENCIES = ["USD", "EUR", "GBP", "CAD", "AUD", "JPY", "MXN", "INR", "CHF", "SGD"]
SETTINGS_FIELDS = [
    ("amadeus_client_id", "Amadeus API client ID"),
    ("amadeus_client_secret", "Amadeus API client secret"),
    ("amadeus_env", "Amadeus environment (test / production)"),
    ("google_headless", "Google Flights headless browser (true / false)"),
    ("google_delay_seconds", "Google Flights seconds between searches"),
]


def to_local(iso: str | None) -> datetime | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso).astimezone()


def fmt_local(iso: str | None) -> str:
    local = to_local(iso)
    return local.strftime("%Y-%m-%d %H:%M") if local else ""


def fmt_minutes(minutes: int | None) -> str:
    if minutes is None:
        return ""
    return f"{minutes // 60}h {minutes % 60:02d}m"


# Results table: (column id, heading, width). Order matches result_values().
RESULT_COLS = [
    ("price", "Price", 85), ("currency", "Cur", 50), ("depart", "Depart", 95), ("out_time", "Out", 60),
    ("return", "Return", 95), ("ret_time", "Back", 60), ("airlines", "Airlines", 110),
    ("flights", "Flight #s", 150), ("stops", "Stops", 70), ("duration", "Duration", 80),
    ("vendor", "Vendor", 180), ("seen", "Last seen", 130), ("provider", "Source", 110),
    ("link", "Buy link", 80),
]


def result_values(o: dict) -> tuple:
    stops = "?" if o["outbound_stops"] is None else str(o["outbound_stops"])
    if o["return_stops"] is not None:
        stops += f" / {o['return_stops']}"
    return (
        f"{o['price']:,.2f}", o["currency"], o["depart_date"], leg_clock(o, 0),
        o["return_date"] or "one-way", leg_clock(o, 1),
        o["airlines"] or "n/a", o["flight_numbers"] or "n/a", stops,
        fmt_minutes(o["duration_minutes"]), o["vendor"], fmt_local(o["scraped_at"]), o["provider"],
        "fare" if buy_url_is_exact(o) else "search",
    )


def result_sort_key(o: dict, col: str):
    if col == "price":
        return o["price"]
    if col == "depart":
        return o["depart_date"]
    if col == "return":
        return o["return_date"] or ""
    if col == "out_time":
        return leg_clock(o, 0)
    if col == "ret_time":
        return leg_clock(o, 1)
    if col == "duration":
        return o["duration_minutes"] if o["duration_minutes"] is not None else float("inf")
    if col == "stops":
        return o["outbound_stops"] if o["outbound_stops"] is not None else 99
    if col == "seen":
        return o["scraped_at"]
    if col == "link":
        return "fare" if buy_url_is_exact(o) else "search"
    text_fields = {"currency": "currency", "airlines": "airlines", "flights": "flight_numbers",
                   "vendor": "vendor", "provider": "provider"}
    return str(o.get(text_fields.get(col, col)) or "")


def parse_optional_date(text: str, label: str, errors: list[str]) -> date | None:
    text = text.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        errors.append(f"{label}: use the format YYYY-MM-DD.")
        return None


def parse_optional_int(text: str, label: str, errors: list[str], default: int | None = None) -> int | None:
    text = text.strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        errors.append(f"{label}: must be a whole number.")
        return default


class FlightTrackerApp:
    def __init__(self, root: tk.Tk, store: Store, service: TrackerService, events: queue.Queue):
        self.root = root
        self.store = store
        self.service = service
        self.events = events
        self.editing_id: int | None = None
        self.status = tk.StringVar(value="Ready. Fill in the Search Setup tab to create a tracker.")
        self.track_choices: dict[str, int] = {}
        self._run_progress: dict[int, tuple[int, int, int]] = {}  # track_id -> (done, total, offers)
        self._dirty_tracks: set[int] = set()
        self._refresh_job: str | None = None
        self._spin_job: str | None = None
        self._spin_frame = 0
        self._seen_offer_ids: set[str] | None = None
        self._row_tags: dict[str, tuple] = {}
        self._toast_box: tk.Frame | None = None

        root.title("Flight Price Tracker")
        root.geometry("1220x800")
        root.minsize(1000, 680)

        self._build_menu()
        bar = ttk.Frame(root, padding=(6, 2))
        bar.pack(side="bottom", fill="x")
        ttk.Label(bar, textvariable=self.status, anchor="w", relief="sunken", padding=(6, 2)
                  ).pack(side="left", fill="x", expand=True)
        # Shown only while a tracker is searching.
        self.progress = ttk.Progressbar(bar, length=240, mode="determinate")
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)
        self.setup_tab = ttk.Frame(self.notebook, padding=10)
        self.trackers_tab = ttk.Frame(self.notebook, padding=10)
        self.results_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.setup_tab, text="Search Setup")
        self.notebook.add(self.trackers_tab, text="Trackers")
        self.notebook.add(self.results_tab, text="Results")
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self.refresh_all())

        self._build_setup()
        self._build_trackers()
        self._build_results()
        self.refresh_all()
        self.root.bind("<F5>", lambda _e: self.refresh_all())
        self.root.bind("<Control-Return>", self._ctrl_enter)
        self.root.after(100, self._poll_events)
        self.root.after(30000, self._periodic_refresh)

    def _ctrl_enter(self, _event=None) -> None:
        """Ctrl+Enter on the Search Setup tab saves the form and runs it right away."""
        if self.notebook.select() == str(self.setup_tab):
            self.save_track(True)

    # ---- menu & settings ---------------------------------------------------
    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="API & scraper settings...", command=self.open_settings)
        menubar.add_cascade(label="Settings", menu=settings_menu)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Quit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)
        self.root.config(menu=menubar)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_close(self) -> None:
        self.service.shutdown()
        self.root.destroy()

    def open_settings(self) -> None:
        current = self.store.get_settings()
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.transient(self.root)
        frame = ttk.Frame(win, padding=12)
        frame.pack(fill="both", expand=True)
        vars_: dict[str, tk.StringVar] = {}
        for row, (key, label) in enumerate(SETTINGS_FIELDS):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=4)
            var = tk.StringVar(value=current.get(key, ""))
            vars_[key] = var
            ttk.Entry(frame, textvariable=var, width=44,
                      show="*" if "secret" in key else "").grid(row=row, column=1, padx=4, pady=4)
        hint = ("Leave Amadeus fields blank to use the AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET "
                "environment variables instead. Secrets are stored in plain text in the local database.")
        ttk.Label(frame, text=hint, wraplength=460, foreground="gray").grid(
            row=len(SETTINGS_FIELDS), column=0, columnspan=2, sticky="w", pady=(8, 4))

        def save() -> None:
            self.store.set_settings({k: v.get().strip() for k, v in vars_.items()})
            self.status.set("Settings saved.")
            win.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=len(SETTINGS_FIELDS) + 1, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Cancel", command=win.destroy).pack(side="right", padx=4)
        ttk.Button(buttons, text="Save", command=save).pack(side="right", padx=4)

    # ---- Search Setup tab --------------------------------------------------
    def _build_setup(self) -> None:
        f = self.setup_tab
        self.v_name = tk.StringVar()
        self.v_origins = tk.StringVar()
        self.v_destinations = tk.StringVar()
        self.v_mode = tk.StringVar(value="strict")
        self.v_depart = tk.StringVar()
        self.v_return = tk.StringVar()
        self.v_dep_from = tk.StringVar()
        self.v_dep_to = tk.StringVar()
        self.v_nights_min = tk.StringVar()
        self.v_nights_max = tk.StringVar()
        self.v_adults = tk.StringVar(value="1")
        self.v_children = tk.StringVar(value="0")
        self.v_cabin = tk.StringVar(value="ECONOMY")
        self.v_stops = tk.StringVar(value="Any")
        self.v_include = tk.StringVar()
        self.v_exclude = tk.StringVar()
        self.v_budget = tk.StringVar()
        self.v_currency = tk.StringVar(value="USD")
        self.v_interval = tk.StringVar(value="360")
        self.v_max_combos = tk.StringVar(value="30")
        self.v_providers = {key: tk.BooleanVar(value=(key == "demo")) for key in REGISTRY}

        # Left and right panels share a draggable sash.
        panes = ttk.PanedWindow(f, orient="horizontal")
        panes.grid(row=0, column=0, columnspan=2, sticky="nsew")

        # Left: route and dates
        left = ttk.LabelFrame(panes, text="Route & dates", padding=10)
        panes.add(left, weight=1)
        self._row(left, 0, "Tracker name", ttk.Entry(left, textvariable=self.v_name, width=42))
        self._row(left, 1, "From (origins)", ttk.Entry(left, textvariable=self.v_origins, width=42),
                  "JFK, New York, KLAX  (IATA, ICAO or city)")
        self._row(left, 2, "To (destinations)", ttk.Entry(left, textvariable=self.v_destinations, width=42),
                  "LAX, Tokyo, EGLL")
        self.preview = ttk.Label(left, text="", foreground="#1f5f8b", wraplength=420)
        self.preview.grid(row=3, column=1, columnspan=2, sticky="w", padx=4)
        self.v_origins.trace_add("write", lambda *_: self._update_preview())
        self.v_destinations.trace_add("write", lambda *_: self._update_preview())

        mode = ttk.Frame(left)
        mode.grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 4))
        ttk.Label(mode, text="Date mode:").pack(side="left", padx=(0, 8))
        ttk.Radiobutton(mode, text="Strict dates", value="strict", variable=self.v_mode,
                        command=self._update_mode_state).pack(side="left", padx=4)
        ttk.Radiobutton(mode, text="Flexible range", value="flexible", variable=self.v_mode,
                        command=self._update_mode_state).pack(side="left", padx=4)

        self.strict_entries = []
        self.flex_entries = []
        self.strict_entries.append(self._entry_row(left, 5, "Depart date", self.v_depart, "YYYY-MM-DD"))
        self.strict_entries.append(self._entry_row(left, 6, "Return date", self.v_return,
                                                   "YYYY-MM-DD, blank = one-way"))
        self.flex_entries.append(self._entry_row(left, 7, "Depart window from", self.v_dep_from, "YYYY-MM-DD"))
        self.flex_entries.append(self._entry_row(left, 8, "Depart window to", self.v_dep_to, "YYYY-MM-DD"))
        self.flex_entries.append(self._entry_row(left, 9, "Trip length min (nights)", self.v_nights_min,
                                                 "blank = one-way"))
        self.flex_entries.append(self._entry_row(left, 10, "Trip length max (nights)", self.v_nights_max, ""))
        left.columnconfigure(1, weight=1)

        # Right: passengers, filters, schedule
        right = ttk.LabelFrame(panes, text="Passengers, filters & schedule", padding=10)
        panes.add(right, weight=1)
        ttk.Spinbox(right, from_=1, to=9, textvariable=self.v_adults, width=8)\
            .grid(row=0, column=1, sticky="w", padx=4, pady=3)
        ttk.Label(right, text="Adults").grid(row=0, column=0, sticky="w", padx=4, pady=3)
        ttk.Spinbox(right, from_=0, to=9, textvariable=self.v_children, width=8)\
            .grid(row=1, column=1, sticky="w", padx=4, pady=3)
        ttk.Label(right, text="Children (2-11)").grid(row=1, column=0, sticky="w", padx=4, pady=3)
        self._row(right, 2, "Cabin", ttk.Combobox(right, textvariable=self.v_cabin, values=CABINS,
                                                  state="readonly", width=20))
        self._row(right, 3, "Max stops", ttk.Combobox(right, textvariable=self.v_stops,
                                                      values=list(STOP_CHOICES), state="readonly", width=20))
        self._row(right, 4, "Only these airlines", ttk.Entry(right, textvariable=self.v_include, width=24),
                  "codes, e.g. DL, UA")
        self._row(right, 5, "Exclude airlines", ttk.Entry(right, textvariable=self.v_exclude, width=24))
        self._row(right, 6, "Budget (alert at or below)", ttk.Entry(right, textvariable=self.v_budget, width=24))
        self._row(right, 7, "Currency", ttk.Combobox(right, textvariable=self.v_currency, values=CURRENCIES,
                                                     width=8))
        self._row(right, 8, "Check every (minutes)", ttk.Entry(right, textvariable=self.v_interval, width=24),
                  "minimum 5")
        self._row(right, 9, "Max dates per route", ttk.Entry(right, textvariable=self.v_max_combos, width=24),
                  "caps API / scrape volume")
        ttk.Label(right, text="Price sources").grid(row=10, column=0, sticky="nw", padx=4, pady=3)
        sources = ttk.Frame(right)
        sources.grid(row=10, column=1, columnspan=2, sticky="w")
        for key, cls in REGISTRY.items():
            ttk.Checkbutton(sources, text=cls.label, variable=self.v_providers[key]).pack(anchor="w")
        right.columnconfigure(1, weight=1)

        buttons = ttk.Frame(f)
        buttons.grid(row=1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Clear form", command=self.clear_form).pack(side="left", padx=4)
        ttk.Button(buttons, text="Save tracker", command=lambda: self.save_track(False)).pack(side="left", padx=4)
        ttk.Button(buttons, text="Save & run now", command=lambda: self.save_track(True)).pack(side="left", padx=4)

        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)
        self._update_mode_state()

    def _row(self, parent, row: int, label: str, widget, hint: str | None = None) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=3)
        widget.grid(row=row, column=1, sticky="ew", padx=4, pady=3)
        if hint:
            ttk.Label(parent, text=hint, foreground="gray").grid(row=row, column=2, sticky="w", padx=4)

    def _entry_row(self, parent, row: int, label: str, var: tk.StringVar, hint: str) -> ttk.Entry:
        entry = ttk.Entry(parent, textvariable=var, width=42)
        self._row(parent, row, label, entry, hint or None)
        return entry

    def _update_mode_state(self) -> None:
        strict = self.v_mode.get() == "strict"
        for e in self.strict_entries:
            e.configure(state="normal" if strict else "disabled")
        for e in self.flex_entries:
            e.configure(state="disabled" if strict else "normal")

    def _update_preview(self) -> None:
        parts = []
        for label, var in (("From", self.v_origins), ("To", self.v_destinations)):
            if var.get().strip():
                try:
                    codes = resolve_airports(var.get())
                    parts.append(f"{label}: " + "; ".join(describe(c) for c in codes))
                except ValueError as exc:
                    parts.append(f"{label}: {exc}")
        self.preview.configure(text="  |  ".join(parts))

    def build_spec(self) -> SearchSpec:
        errors: list[str] = []
        origins: list[str] = []
        destinations: list[str] = []
        try:
            origins = resolve_airports(self.v_origins.get())
        except ValueError as exc:
            errors.append(f"From: {exc}")
        try:
            destinations = resolve_airports(self.v_destinations.get())
        except ValueError as exc:
            errors.append(f"To: {exc}")

        strict = self.v_mode.get() == "strict"
        depart = return_ = dep_from = dep_to = None
        nights_min = nights_max = None
        if strict:
            depart = parse_optional_date(self.v_depart.get(), "Depart date", errors)
            return_ = parse_optional_date(self.v_return.get(), "Return date", errors)
        else:
            dep_from = parse_optional_date(self.v_dep_from.get(), "Depart window from", errors)
            dep_to = parse_optional_date(self.v_dep_to.get(), "Depart window to", errors)
            nights_min = parse_optional_int(self.v_nights_min.get(), "Trip length min", errors)
            nights_max = parse_optional_int(self.v_nights_max.get(), "Trip length max", errors)

        adults = parse_optional_int(self.v_adults.get(), "Adults", errors, 1) or 0
        children = parse_optional_int(self.v_children.get(), "Children", errors, 0) or 0
        interval = parse_optional_int(self.v_interval.get(), "Check interval", errors, 360) or 0
        max_combos = parse_optional_int(self.v_max_combos.get(), "Max dates per route", errors, 30) or 0
        budget = None
        if self.v_budget.get().strip():
            try:
                budget = float(self.v_budget.get().replace(",", ""))
            except ValueError:
                errors.append("Budget: must be a number.")

        def codes(text: str) -> list[str]:
            return [c.strip().upper() for c in text.split(",") if c.strip()]

        providers = [k for k, var in self.v_providers.items() if var.get()]
        if errors:
            raise ValueError("\n".join(errors))

        spec = SearchSpec(
            name=self.v_name.get().strip(),
            origins=origins, destinations=destinations,
            date_mode=self.v_mode.get(),
            depart_date=depart, return_date=return_,
            depart_from=dep_from, depart_to=dep_to,
            nights_min=nights_min, nights_max=nights_max,
            adults=adults, children=children, cabin=self.v_cabin.get(),
            max_stops=STOP_CHOICES.get(self.v_stops.get()),
            include_airlines=codes(self.v_include.get()),
            exclude_airlines=codes(self.v_exclude.get()),
            budget=budget, currency=self.v_currency.get().strip().upper() or "USD",
            providers=providers, interval_minutes=interval, max_date_combinations=max_combos,
        )
        spec.validate()
        return spec

    def save_track(self, run_now: bool) -> None:
        try:
            spec = self.build_spec()
        except ValueError as exc:
            messagebox.showerror("Check the form", str(exc), parent=self.root)
            return
        settings = self.store.effective_settings()
        problems = []
        for key in spec.providers:
            missing = create_provider(key, settings).missing_config()
            if missing:
                problems.append(f"{REGISTRY[key].label} needs: {', '.join(missing)}")
        if problems:
            proceed = messagebox.askyesno(
                "Missing API settings",
                "\n".join(problems) + "\n\nThose sources will record an error on every run. "
                "Open Settings → API & scraper settings to add them.\n\nSave anyway?",
                parent=self.root)
            if not proceed:
                return
        if self.editing_id is not None:
            track_id = self.editing_id
            self.store.update_track(track_id, spec.name, spec)
        else:
            track_id = self.store.add_track(spec.name, spec)
        self.editing_id = None
        self.refresh_tracks()
        if run_now:
            self.service.run_now(track_id)
            self.show_results_for(track_id)
            self.notebook.select(self.results_tab)
            self.status.set(f"Saved '{spec.name}'. Results will appear here as each search finishes.")
        else:
            self.status.set(f"Saved '{spec.name}'. It will run on its schedule.")

    def clear_form(self) -> None:
        self.editing_id = None
        for var in (self.v_name, self.v_origins, self.v_destinations, self.v_depart, self.v_return,
                    self.v_dep_from, self.v_dep_to, self.v_nights_min, self.v_nights_max,
                    self.v_include, self.v_exclude, self.v_budget):
            var.set("")
        self.v_mode.set("strict")
        self.v_adults.set("1")
        self.v_children.set("0")
        self.v_cabin.set("ECONOMY")
        self.v_stops.set("Any")
        self.v_currency.set("USD")
        self.v_interval.set("360")
        self.v_max_combos.set("30")
        for key, var in self.v_providers.items():
            var.set(key == "demo")
        self._update_mode_state()
        self._update_preview()

    def load_into_form(self, track_id: int) -> None:
        track = self.store.get_track(track_id)
        if not track:
            return
        s: SearchSpec = track["spec"]
        self.clear_form()
        self.editing_id = track_id
        self.v_name.set(track["name"])
        self.v_origins.set(", ".join(s.origins))
        self.v_destinations.set(", ".join(s.destinations))
        self.v_mode.set(s.date_mode)
        iso = lambda d: d.isoformat() if d else ""  # noqa: E731
        self.v_depart.set(iso(s.depart_date))
        self.v_return.set(iso(s.return_date))
        self.v_dep_from.set(iso(s.depart_from))
        self.v_dep_to.set(iso(s.depart_to))
        self.v_nights_min.set("" if s.nights_min is None else str(s.nights_min))
        self.v_nights_max.set("" if s.nights_max is None else str(s.nights_max))
        self.v_adults.set(str(s.adults))
        self.v_children.set(str(s.children))
        self.v_cabin.set(s.cabin)
        self.v_stops.set(next(k for k, v in STOP_CHOICES.items() if v == s.max_stops))
        self.v_include.set(", ".join(s.include_airlines))
        self.v_exclude.set(", ".join(s.exclude_airlines))
        self.v_budget.set("" if s.budget is None else str(s.budget))
        self.v_currency.set(s.currency)
        self.v_interval.set(str(s.interval_minutes))
        self.v_max_combos.set(str(s.max_date_combinations))
        for key, var in self.v_providers.items():
            var.set(key in s.providers)
        self._update_mode_state()
        self._update_preview()
        self.notebook.select(self.setup_tab)
        self.status.set(f"Editing '{track['name']}'. Press Save tracker to apply changes.")

    # ---- Trackers tab ------------------------------------------------------
    def _build_trackers(self) -> None:
        f = self.trackers_tab
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Run now", command=self.run_selected).pack(side="left", padx=2)
        self.btn_toggle = ttk.Button(bar, text="Pause / resume", command=self.toggle_selected)
        self.btn_toggle.pack(side="left", padx=2)
        ttk.Button(bar, text="Edit in form", command=self.edit_selected).pack(side="left", padx=2)
        ttk.Button(bar, text="Delete", command=self.delete_selected).pack(side="left", padx=2)
        ttk.Button(bar, text="Refresh", command=self.refresh_all).pack(side="left", padx=2)

        # Tracker list and run history are split by a draggable sash.
        panes = ttk.PanedWindow(f, orient="vertical")
        panes.pack(fill="both", expand=True)
        top = ttk.Frame(panes)
        bottom = ttk.Frame(panes)
        panes.add(top, weight=1)
        panes.add(bottom, weight=1)

        cols = ("id", "name", "route", "mode", "sources", "every", "active", "last_run", "created")
        self.tree_tracks = self._tree(top, cols, height=10, widths={
            "id": 40, "name": 200, "route": 200, "mode": 190, "sources": 150, "every": 70,
            "active": 60, "last_run": 130, "created": 130})
        self.tree_tracks.master.pack(fill="both", expand=True)
        self.tree_tracks.bind("<<TreeviewSelect>>", lambda _e: self.refresh_runs())

        ttk.Label(bottom, text="Run history for the selected tracker", font=("TkDefaultFont", 10, "bold")
                  ).pack(anchor="w", pady=(10, 4))
        rcols = ("started", "trigger", "status", "queries", "offers", "errors")
        self.tree_runs = self._tree(bottom, rcols, height=10, widths={
            "started": 150, "trigger": 90, "status": 80, "queries": 80, "offers": 80, "errors": 600})
        self.tree_runs.master.pack(fill="both", expand=True)

    def _tree(self, parent, cols, height: int, widths: dict[str, int]) -> ttk.Treeview:
        """Build a scrollable table. The caller packs or adds `tree.master` (its frame)."""
        frame = ttk.Frame(parent)
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=height, selectmode="browse")
        for c in cols:
            tree.heading(c, text=c.replace("_", " ").capitalize())
            tree.column(c, width=widths.get(c, 100), stretch=(c == cols[-1]), anchor="w")
        ysb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        xsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return tree

    def _selected_track_id(self) -> int | None:
        sel = self.tree_tracks.selection()
        return int(sel[0]) if sel else None

    def refresh_tracks(self) -> None:
        selected = self._selected_track_id()
        self.tree_tracks.delete(*self.tree_tracks.get_children())
        self.track_choices = {}
        for t in self.store.list_tracks():
            s: SearchSpec = t["spec"]
            route = f"{','.join(s.origins)} → {','.join(s.destinations)}"
            if s.date_mode == "strict":
                dates = f"{s.depart_date}" + (f" → {s.return_date}" if s.return_date else " (one-way)")
            else:
                trip = (f"{s.nights_min}-{s.nights_max} nights" if s.nights_min is not None else "one-way")
                dates = f"{s.depart_from}..{s.depart_to}, {trip}"
            self.tree_tracks.insert("", "end", iid=str(t["id"]), values=(
                t["id"], t["name"], route, dates, ", ".join(s.providers), f"{t['interval_minutes']} min",
                self._state_text(t), fmt_local(t["last_run_at"]) or "never", fmt_local(t["created_at"])))
            self.track_choices[f"{t['id']}: {t['name']}"] = t["id"]
        if selected is not None and self.tree_tracks.exists(str(selected)):
            self.tree_tracks.selection_set(str(selected))
        self.refresh_runs()
        if hasattr(self, "track_combo"):
            self.track_combo["values"] = list(self.track_choices)

    def _state_text(self, track: dict) -> str:
        progress = self._run_progress.get(track["id"])
        if progress is not None:
            done, total, offers = progress
            spin = SPINNER[self._spin_frame % len(SPINNER)]
            return f"{spin} {done}/{total} · {offers} offers"
        if self.service.is_running(track["id"]):
            return f"{SPINNER[self._spin_frame % len(SPINNER)]} queued"
        return "yes" if track["active"] else "paused"

    def _update_running_rows(self) -> None:
        """Animate the state cell of running trackers without rebuilding the table."""
        for track_id in list(self._run_progress):
            iid = str(track_id)
            if self.tree_tracks.exists(iid):
                track = self.store.get_track(track_id)
                if track is not None:
                    self.tree_tracks.set(iid, "active", self._state_text(track))

    def _tick_spinner(self) -> None:
        self._spin_frame += 1
        self._update_running_rows()
        if self._run_progress or self.service.is_running_any():
            self._spin_job = self.root.after(250, self._tick_spinner)
        else:
            self._spin_job = None

    def _ensure_spinner(self) -> None:
        if self._spin_job is None:
            self._spin_job = self.root.after(250, self._tick_spinner)

    def refresh_runs(self) -> None:
        self.tree_runs.delete(*self.tree_runs.get_children())
        track_id = self._selected_track_id()
        if track_id is None:
            return
        for r in self.store.list_runs(track_id):
            errors = (r["errors"] or "").replace("\n", " | ")
            self.tree_runs.insert("", "end", values=(
                fmt_local(r["started_at"]), r["trigger"], r["status"], r["query_count"],
                r["offer_count"], errors))

    def run_selected(self) -> None:
        track_id = self._selected_track_id()
        if track_id is None:
            messagebox.showinfo("Select a tracker", "Select a tracker first.", parent=self.root)
            return
        if self.service.run_now(track_id):
            self.status.set("Run started in the background...")
            self.show_results_for(track_id)  # so the Results tab is already on it when you switch
        else:
            self.status.set("That tracker is already running.")
        self.refresh_tracks()

    def toggle_selected(self) -> None:
        track_id = self._selected_track_id()
        if track_id is None:
            return
        track = self.store.get_track(track_id)
        self.store.set_track_active(track_id, not track["active"])
        self.refresh_tracks()

    def edit_selected(self) -> None:
        track_id = self._selected_track_id()
        if track_id is not None:
            self.load_into_form(track_id)

    def delete_selected(self) -> None:
        track_id = self._selected_track_id()
        if track_id is None:
            return
        if messagebox.askyesno("Delete tracker",
                               "Delete this tracker and all of its stored prices?", parent=self.root):
            self.store.delete_track(track_id)
            self.refresh_tracks()
            self.refresh_results()

    # ---- Results tab -------------------------------------------------------
    def _build_results(self) -> None:
        f = self.results_tab
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Tracker:").pack(side="left")
        self.track_var = tk.StringVar()
        self.track_combo = ttk.Combobox(bar, textvariable=self.track_var, state="readonly", width=50)
        self.track_combo.pack(side="left", padx=6)
        self.track_combo.bind("<<ComboboxSelected>>", lambda _e: self.refresh_results())
        ttk.Button(bar, text="Refresh", command=self.refresh_results).pack(side="left", padx=2)
        ttk.Button(bar, text="Export CSV...", command=self.export_csv).pack(side="left", padx=2)
        ttk.Button(bar, text="Chart CSV...", command=self.chart_csv).pack(side="left", padx=2)
        self.v_best = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Best price per trip", variable=self.v_best,
                        command=self.refresh_results).pack(side="left", padx=(12, 2))
        self.live_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.live_var, foreground="#1f5f8b").pack(side="right", padx=4)

        # Offers table, chart and details are split by draggable sashes.
        panes = ttk.PanedWindow(f, orient="vertical")
        panes.pack(fill="both", expand=True)
        table_pane = ttk.Frame(panes)
        panes.add(table_pane, weight=3)
        self.tree_offers = self._tree(table_pane, [c for c, _label, _w in RESULT_COLS], height=14,
                                      widths={c: w for c, _label, w in RESULT_COLS})
        self.tree_offers.master.pack(fill="both", expand=True)
        for col, label, _w in RESULT_COLS:
            self.tree_offers.heading(col, text=label, command=lambda c=col: self.sort_results(c))
        self.tree_offers.tag_configure("best", background="#e6f4ea")
        self.tree_offers.tag_configure("flash", background="#fff3b0")
        self.tree_offers.bind("<<TreeviewSelect>>", lambda _e: self.show_offer_detail())
        self.tree_offers.bind("<Double-Button-1>", lambda _e: self.open_buy_page())
        self.results_sort = ("price", False)
        self._offers_shown: dict[str, dict] = {}

        bottom = ttk.PanedWindow(panes, orient="horizontal")
        panes.add(bottom, weight=2)
        self.chart = tk.Canvas(bottom, height=200, background="white", highlightthickness=1,
                               highlightbackground="#ccc")
        self.chart.bind("<Configure>", lambda _e: self._redraw_history())
        bottom.add(self.chart, weight=1)
        side = ttk.Frame(bottom)
        bottom.add(side, weight=1)
        buttons = ttk.Frame(side)
        buttons.pack(fill="x", pady=(0, 4))
        ttk.Button(buttons, text="Open buy page", command=self.open_buy_page).pack(side="left", padx=2)
        ttk.Button(buttons, text="Copy link", command=self.copy_buy_link).pack(side="left", padx=2)
        self.detail = tk.Text(side, height=10, width=70, wrap="word", font=("Consolas", 9))
        self.detail.pack(fill="both", expand=True)

    def _redraw_history(self) -> None:
        """Redraw the price chart at the current canvas size (called on resize)."""
        track_id = self._track_from_combo()
        if track_id is not None:
            self._draw_history(track_id)

    def refresh_results(self, live: bool = False) -> None:
        """Rebuild the results table. `live` is set for in-run updates: it keeps the selection and
        scroll position and flashes rows that were not shown before."""
        track_id = self._track_from_combo()
        selected = self._selected_offer_id() if live else None
        scroll = self.tree_offers.yview()[0] if live else 0.0
        self.tree_offers.delete(*self.tree_offers.get_children())
        self._offers_shown = {}
        self._row_tags = {}
        if track_id is None:
            self.detail.delete("1.0", "end")
            self.chart.delete("all")
            self._seen_offer_ids = None
            self._update_live_label(None)
            return
        offers = self.store.latest_offers(track_id, limit=None)
        if self.v_best.get():
            shown = best_per_trip(offers)
        else:
            shown = sorted(offers, key=lambda o: o["price"])[:500]
        col, descending = self.results_sort
        shown.sort(key=lambda o: result_sort_key(o, col), reverse=descending)

        ids = {str(o["id"]) for o in shown}
        baseline = self._seen_offer_ids if live else None
        fresh = (ids - baseline) if baseline is not None else set()
        cheapest = min((o["price"] for o in shown), default=None)
        for o in shown:
            iid = str(o["id"])
            self._offers_shown[iid] = o
            base = ("best",) if o["price"] == cheapest else ()
            self._row_tags[iid] = base
            tags = ("flash",) if iid in fresh else base
            self.tree_offers.insert("", "end", iid=iid, values=result_values(o), tags=tags)
        self._seen_offer_ids = ids
        if fresh:
            self.root.after(2000, lambda found=fresh: self._end_flash(found))

        if live:
            self.tree_offers.yview_moveto(scroll)
        if selected in self._offers_shown:
            self.tree_offers.selection_set(selected)
        else:
            self.detail.delete("1.0", "end")
        self._draw_history(track_id)
        self._update_live_label(track_id)

    def _end_flash(self, iids: set[str]) -> None:
        for iid in iids:
            if self.tree_offers.exists(iid):
                self.tree_offers.item(iid, tags=self._row_tags.get(iid, ()))

    def _selected_offer_id(self) -> str | None:
        sel = self.tree_offers.selection()
        return sel[0] if sel else None

    def _update_live_label(self, track_id: int | None) -> None:
        progress = self._run_progress.get(track_id) if track_id is not None else None
        if progress is not None:
            done, total, offers = progress
            self.live_var.set(f"● Live: {done} of {total} searches · {offers} offers so far")
        else:
            self.live_var.set(f"Updated {datetime.now().strftime('%H:%M:%S')}")

    def show_results_for(self, track_id: int) -> None:
        """Point the Results tab at a tracker (used after running one)."""
        label = next((k for k, v in self.track_choices.items() if v == track_id), None)
        if label is not None:
            self.track_var.set(label)
            self.refresh_results()

    def sort_results(self, col: str) -> None:
        current, descending = self.results_sort
        self.results_sort = (col, (not descending) if col == current else False)
        self.refresh_results()

    def _selected_offer(self) -> dict | None:
        sel = self.tree_offers.selection()
        return self._offers_shown.get(sel[0]) if sel else None

    def open_buy_page(self) -> None:
        offer = self._selected_offer()
        if offer is None:
            messagebox.showinfo("Buy", "Select an offer first.", parent=self.root)
            return
        webbrowser.open(buy_url(offer))

    def copy_buy_link(self) -> None:
        offer = self._selected_offer()
        if offer is None:
            messagebox.showinfo("Buy", "Select an offer first.", parent=self.root)
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(buy_url(offer))
        self.status.set("Booking link copied to the clipboard.")

    def _track_from_combo(self) -> int | None:
        return self.track_choices.get(self.track_var.get())

    def show_offer_detail(self) -> None:
        offer = self._selected_offer()
        self.detail.delete("1.0", "end")
        if offer is None:
            return
        route = f"{offer['origin']} → {offer['destination']}  depart {offer['depart_date']}"
        route += f", return {offer['return_date']}" if offer["return_date"] else " (one-way)"
        itinerary = itinerary_lines(offer) or ["(no leg details captured for this source)"]
        lines = [
            route,
            f"{offer['price']:,.2f} {offer['currency']}  ·  {offer['vendor']}  ·  {offer['cabin']}",
            f"Last seen {fmt_local(offer['scraped_at'])}"
            + (f"  ·  {offer['seats_left']} seats left" if offer["seats_left"] is not None else ""),
            "",
            "Itinerary:",
            *("  " + line for line in itinerary),
            "",
        ]
        if buy_url_is_exact(offer):
            lines.append("Booking link (provider's fare):")
        else:
            lines.append("Booking link (Google Flights search for these dates; the fare may differ "
                         "or not be offered there. Confirm the price before buying):")
        lines.append(buy_url(offer))
        self.detail.insert("1.0", "\n".join(lines))

    def _draw_history(self, track_id: int) -> None:
        c = self.chart
        c.delete("all")
        history = self.store.price_history(track_id)
        w = max(c.winfo_width(), 400)
        h = max(c.winfo_height(), 200)
        pad = 50
        if not history:
            c.create_text(w / 2, h / 2, text="No price history yet. Run the tracker to collect prices.",
                          fill="gray")
            return
        prices = [p for _, p in history]
        lo, hi = min(prices), max(prices)
        if hi == lo:
            hi = lo + 1
        n = len(history)

        def xy(i: int, p: float) -> tuple[float, float]:
            x = pad + (w - 2 * pad) * (i / (n - 1) if n > 1 else 0.5)
            y = h - pad - (h - 2 * pad) * (p - lo) / (hi - lo)
            return x, y

        c.create_line(pad, h - pad, w - pad, h - pad, fill="#999")
        c.create_line(pad, pad, pad, h - pad, fill="#999")
        c.create_text(pad - 6, pad, text=f"{hi:,.0f}", anchor="e")
        c.create_text(pad - 6, h - pad, text=f"{lo:,.0f}", anchor="e")
        pts = [xy(i, p) for i, (_, p) in enumerate(history)]
        if n > 1:
            c.create_line(*[v for pt in pts for v in pt], fill="#1f77b4", width=2)
        for x, y in pts:
            c.create_oval(x - 3, y - 3, x + 3, y + 3, fill="#1f77b4", outline="")
        c.create_text(pad, h - pad + 14, text=fmt_local(history[0][0]), anchor="w")
        c.create_text(w - pad, h - pad + 14, text=fmt_local(history[-1][0]), anchor="e")
        c.create_text(w / 2, pad - 18, text="Lowest price found per run")

    def export_csv(self) -> None:
        track_id = self._track_from_combo()
        if track_id is None:
            messagebox.showinfo("Export", "Pick a tracker first.", parent=self.root)
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")],
                                            initialfile=f"tracker_{track_id}_prices.csv")
        if not path:
            return
        rows = self.store.latest_offers(track_id, limit=None)
        for row in rows:
            row.update(csv_extras(row))
        fields = ["scraped_at", "provider", "vendor", "origin", "destination", "depart_date",
                  "return_date", "price", "currency", "airlines", "flight_numbers", "outbound_stops",
                  "return_stops", "duration_minutes", "cabin", "seats_left",
                  "outbound_depart", "return_depart", "buy_url", "buy_url_exact"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        self.status.set(f"Exported {len(rows)} rows to {Path(path).name}.")

    def chart_csv(self) -> None:
        """Build a price-over-time HTML page from an exported CSV and open it in the browser."""
        path = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")], title="Choose an exported CSV")
        if not path:
            return
        try:
            out = write_page(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Chart", str(exc), parent=self.root)
            return
        webbrowser.open(out.resolve().as_uri())
        self.status.set(f"Wrote chart page {out.name}.")

    # ---- refresh & events --------------------------------------------------
    def refresh_all(self) -> None:
        self.refresh_tracks()
        self.refresh_results()

    def _periodic_refresh(self) -> None:
        self.refresh_tracks()
        self.root.after(30000, self._periodic_refresh)

    def _poll_events(self) -> None:
        try:
            while True:
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _request_refresh(self, track_id: int) -> None:
        """Coalesce bursts of events (one per search) into a single table refresh."""
        self._dirty_tracks.add(track_id)
        if self._refresh_job is None:
            self._refresh_job = self.root.after(250, self._flush_refresh)

    def _flush_refresh(self) -> None:
        self._refresh_job = None
        dirty, self._dirty_tracks = self._dirty_tracks, set()
        self.refresh_tracks()
        if self._track_from_combo() in dirty:
            self.refresh_results(live=True)

    def _show_progress(self, done: int, total: int) -> None:
        self.progress.configure(maximum=max(total, 1), value=done)
        if not self.progress.winfo_ismapped():
            self.progress.pack(side="right", padx=(8, 0))

    def _hide_progress(self) -> None:
        if not self._run_progress:
            self.progress.pack_forget()

    def _handle_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "log":
            self.status.set(event.get("message", ""))
            return
        track_id = event.get("track_id")
        if kind == "run_started":
            self._run_progress[track_id] = (0, event["total"], 0)
            self._show_progress(0, event["total"])
            self.status.set(f"'{event['name']}' started ({event['trigger']}): "
                            f"{event['total']} searches. Results appear as they arrive.")
            self._ensure_spinner()
            self._request_refresh(track_id)
            return
        if kind == "progress":
            self._run_progress[track_id] = (event["done"], event["total"], event["offers_so_far"])
            self._show_progress(event["done"], event["total"])
            self.status.set(f"'{event['name']}': {event['done']} of {event['total']} searches done, "
                            f"{event['offers_so_far']} offers so far")
            self._ensure_spinner()
            self._update_live_label(self._track_from_combo())
            self._request_refresh(track_id)
            return
        if kind != "run_done":
            return
        self._run_progress.pop(track_id, None)
        self._hide_progress()
        low = event.get("min_price")
        summary = f"'{event['name']}' finished: {event['offers']} offers"
        if low is not None:
            summary += f", lowest {event['currency']} {low:,.2f}"
        if event["errors"]:
            summary += f", {len(event['errors'])} error(s) - see run history"
        self.status.set(summary)
        self._request_refresh(track_id)
        if event["alerts"]:
            self.root.bell()
            self._toast(f"Price alert: {event['name']}", "\n".join(event["alerts"]), track_id)

    def _toast(self, title: str, message: str, track_id: int | None, seconds: int = 12) -> None:
        """Non-blocking alert in the corner. Click it to jump to that tracker's results."""
        if self._toast_box is not None and self._toast_box.winfo_exists():
            self._toast_box.destroy()
        bg = "#1f5f8b"
        box = tk.Frame(self.root, bg=bg, padx=12, pady=8, cursor="hand2")
        self._toast_box = box
        tk.Label(box, text=title, bg=bg, fg="white", font=("TkDefaultFont", 10, "bold"),
                 justify="left", anchor="w").pack(anchor="w")
        tk.Label(box, text=message, bg=bg, fg="white", justify="left", anchor="w",
                 wraplength=380).pack(anchor="w")
        tk.Label(box, text="Click to view results", bg=bg, fg="#cfe3f3", anchor="w",
                 font=("TkDefaultFont", 8)).pack(anchor="w", pady=(4, 0))

        def open_results(_e=None) -> None:
            if track_id is not None:
                self.show_results_for(track_id)
                self.notebook.select(self.results_tab)
            box.destroy()

        for widget in (box, *box.winfo_children()):
            widget.bind("<Button-1>", open_results)
        box.place(relx=1.0, rely=1.0, x=-16, y=-16, anchor="se")
        self.root.after(seconds * 1000, lambda: box.destroy() if box.winfo_exists() else None)


def run_app(db_path: str | Path) -> None:
    store = Store(db_path)
    store.close_stale_runs()
    events: queue.Queue = queue.Queue()
    service = TrackerService(store, events)
    service.start()
    root = tk.Tk()
    FlightTrackerApp(root, store, service, events)
    root.mainloop()


def default_db_path() -> Path:
    env = os.environ.get("FLIGHT_TRACKER_DB")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "flight_tracker.db"
