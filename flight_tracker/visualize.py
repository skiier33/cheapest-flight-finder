"""Render a price-over-time page from a tracker CSV export.

    python -m flight_tracker.visualize prices.csv [-o page.html] [--no-open]

The output is one self-contained HTML file that needs no network access: the
CSV rows are embedded as JSON and drawn with inline SVG. Series can be grouped
by flight number, airline, vendor, outbound stops, route, or depart date.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_COLUMNS = ("scraped_at", "price")


def _parse_time_ms(text: str | None) -> int | None:
    if not text or not text.strip():
        return None
    try:
        dt = datetime.fromisoformat(text.strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _int_or_none(text: str | None) -> int | None:
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _split_list(text: str | None, sep: str) -> str:
    return sep.join(p.strip() for p in (text or "").split(",") if p.strip())


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    """Read a tracker CSV export into normalized rows. Rows without a time or price are skipped."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path}: missing column(s) {', '.join(missing)}. "
                             "Expected a CSV from Results -> Export CSV.")
        rows: list[dict[str, Any]] = []
        for raw in reader:
            t = _parse_time_ms(raw.get("scraped_at"))
            try:
                price = float(raw.get("price") or "")
            except ValueError:
                price = None
            if t is None or price is None:
                continue
            rows.append({
                "t": t,
                "price": price,
                "currency": (raw.get("currency") or "").strip(),
                "vendor": (raw.get("vendor") or "").strip(),
                "origin": (raw.get("origin") or "").strip(),
                "destination": (raw.get("destination") or "").strip(),
                "depart": (raw.get("depart_date") or "").strip(),
                "ret": (raw.get("return_date") or "").strip(),
                "flights": _split_list(raw.get("flight_numbers"), " · "),
                "airlines": _split_list(raw.get("airlines"), ", "),
                "stops": _int_or_none(raw.get("outbound_stops")),
                "duration": _int_or_none(raw.get("duration_minutes")),
                "cabin": (raw.get("cabin") or "").strip(),
                "out_time": (raw.get("outbound_depart") or "").strip(),
                "ret_time": (raw.get("return_depart") or "").strip(),
                "buy_url": (raw.get("buy_url") or "").strip(),
            })
    return rows


def build_html(rows: list[dict[str, Any]], title: str) -> str:
    # Escape "<", ">" and "&" so the embedded JSON can never close the script tag.
    data = (json.dumps(rows)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))
    return (TEMPLATE
            .replace("__TITLE__", html.escape(title))
            .replace("__DATA__", data))


def write_page(csv_path: str | Path, out_path: str | Path | None = None) -> Path:
    src = Path(csv_path)
    out = Path(out_path) if out_path else src.with_suffix(".html")
    rows = load_rows(src)
    out.write_text(build_html(rows, f"Flight prices: {src.name}"), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build a price-over-time page from a tracker CSV.")
    parser.add_argument("csv", help="CSV exported from Results -> Export CSV")
    parser.add_argument("-o", "--output", help="HTML output path (default: next to the CSV)")
    parser.add_argument("--no-open", action="store_true", help="Do not open the page in a browser")
    args = parser.parse_args(argv)
    out = write_page(args.csv, args.output)
    print(f"Wrote {out}")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    color-scheme: light;
    --surface: #fcfcfb;
    --text: #0b0b0b;
    --text2: #52514e;
    --muted: #6b6a66;
    --grid: #e4e3df;
    --border: #d8d7d2;
    --control: #ffffff;
    --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a; --series-4: #eda100;
    --series-5: #e87ba4; --series-6: #008300; --series-7: #6250d6; --series-8: #e34948;
    --series-other: #9a9893;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --surface: #1a1a19; --text: #f0efec; --text2: #c3c2b7; --muted: #a3a29a;
      --grid: #2e2e2b; --border: #3a3a37; --control: #232321;
      --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500;
      --series-5: #d55181; --series-6: #0ca30c; --series-7: #9085e9; --series-8: #e66767;
      --series-other: #7d7c77;
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --surface: #1a1a19; --text: #f0efec; --text2: #c3c2b7; --muted: #a3a29a;
    --grid: #2e2e2b; --border: #3a3a37; --control: #232321;
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500;
    --series-5: #d55181; --series-6: #0ca30c; --series-7: #9085e9; --series-8: #e66767;
    --series-other: #7d7c77;
  }
  * { box-sizing: border-box; }
  [hidden] { display: none !important; }
  body {
    margin: 0; padding: 24px; background: var(--surface); color: var(--text);
    font: 14px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  h1 { font-size: 20px; margin: 0 0 4px; }
  h2 { font-size: 14px; margin: 0 0 8px; }
  .summary { color: var(--text2); margin: 0 0 16px; }
  .controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; margin-bottom: 16px; }
  .controls label { display: flex; flex-direction: column; gap: 4px; color: var(--text2); font-size: 12px; }
  select, button {
    font: inherit; color: var(--text); background: var(--control);
    border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px;
  }
  button { cursor: pointer; }
  .layout { display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 24px; align-items: start; }
  @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
  .chart-wrap { position: relative; min-height: 200px; }
  svg { width: 100%; height: auto; display: block; }
  .grid { stroke: var(--grid); stroke-width: 1; }
  .axis { stroke: var(--border); stroke-width: 1; }
  .tick { fill: var(--text2); font-size: 12px; }
  .series-line { fill: none; stroke-width: 2; }
  .dot { stroke: var(--surface); stroke-width: 1.5; }
  .hit { fill: transparent; cursor: pointer; }
  .empty { color: var(--text2); padding: 48px 0; text-align: center; }
  .legend { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }
  .legend label {
    display: grid; grid-template-columns: auto auto 1fr; gap: 8px; align-items: center;
    padding: 6px 8px; border-radius: 6px; cursor: pointer;
  }
  .legend label:hover { background: var(--grid); }
  .legend .name { overflow-wrap: anywhere; }
  .legend .meta { grid-column: 3; color: var(--text2); font-size: 12px; }
  .legend .off .name, .legend .off .meta { color: var(--muted); text-decoration: line-through; }
  .swatch { display: inline-block; width: 12px; height: 12px; border-radius: 3px; }
  .note { color: var(--text2); font-size: 12px; margin: 12px 0 0; }
  .table-wrap { margin-top: 24px; overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--grid); white-space: nowrap; }
  th { color: var(--text2); font-weight: 600; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  footer { color: var(--muted); font-size: 12px; margin-top: 24px; }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <p class="summary" id="summary"></p>
</header>

<section class="controls" aria-label="Filters">
  <label>Group series by
    <select id="group"></select>
  </label>
  <label id="currencyWrap">Currency
    <select id="currency"></select>
  </label>
  <label>Depart date
    <select id="depart"></select>
  </label>
  <button type="button" id="tableToggle" aria-expanded="false">Show table</button>
</section>

<div class="layout">
  <div class="chart-wrap">
    <svg id="chart" viewBox="0 0 960 420" role="img" aria-label="Price over time by series"></svg>
    <p class="empty" id="empty" hidden></p>
  </div>
  <aside>
    <h2>Series</h2>
    <ul class="legend" id="legend"></ul>
    <p class="note" id="note"></p>
  </aside>
</div>

<section class="table-wrap" id="tableWrap" hidden>
  <table>
    <thead id="tableHead"></thead>
    <tbody id="tableBody"></tbody>
  </table>
</section>

<footer>
  Each dot is one offer. The line joins the lowest price found in each scrape.
  Hover a dot for details. Series colours follow the cheapest-first order of the
  current grouping; series beyond the first eight are combined into "Other".
</footer>

<noscript>This page needs JavaScript to draw the chart.</noscript>

<script>
(function () {
  "use strict";
  var NS = "http://www.w3.org/2000/svg";
  var DATA = __DATA__;
  var MAX_SERIES = 8;
  var OTHER_ID = "__other__";
  var UNKNOWN = "(unknown)";
  var W = 960, H = 420, M = { l: 76, r: 18, t: 16, b: 42 };

  function stopsLabel(s) {
    if (s === null || s === undefined) return UNKNOWN;
    if (s === 0) return "Nonstop";
    return s + (s === 1 ? " stop" : " stops");
  }

  var GROUPS = {
    flight:  { label: "Flight numbers", key: function (r) { return r.flights || UNKNOWN; } },
    airline: { label: "Airlines", key: function (r) { return r.airlines || UNKNOWN; } },
    vendor:  { label: "Vendor", key: function (r) { return r.vendor || UNKNOWN; } },
    stops:   { label: "Outbound stops", key: function (r) { return stopsLabel(r.stops); } },
    route:   { label: "Route", key: function (r) { return r.origin + " → " + r.destination; } },
    depart:  { label: "Depart date", key: function (r) { return r.depart || UNKNOWN; } }
  };

  function $(id) { return document.getElementById(id); }

  function money(v, cur, digits) {
    try {
      return new Intl.NumberFormat(undefined, {
        style: "currency", currency: cur,
        minimumFractionDigits: digits, maximumFractionDigits: digits
      }).format(v);
    } catch (e) {
      return v.toFixed(digits) + (cur ? " " + cur : "");
    }
  }
  function fmtTick(ms) {
    return new Date(ms).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  }
  function fmtFull(ms) {
    return new Date(ms).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  }
  function fmtDur(min) {
    if (!min) return "";
    var h = Math.floor(min / 60), m = min % 60;
    return (h ? h + "h " : "") + (m ? m + "m" : "");
  }
  function niceStep(span, count) {
    var raw = span / count;
    var p = Math.pow(10, Math.floor(Math.log10(raw)));
    var f = raw / p;
    var m = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
    return m * p;
  }
  function el(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function makeEl(tag, s, parent) {
    var e = document.createElement(tag);
    if (s !== undefined) e.textContent = s;
    if (parent) parent.appendChild(e);
    return e;
  }
  function currencyOf(r) { return r.currency || ""; }

  // ---- state and controls ------------------------------------------------
  var counts = {};
  DATA.forEach(function (r) { var c = currencyOf(r); counts[c] = (counts[c] || 0) + 1; });
  var currencies = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a] || a.localeCompare(b); });
  var state = { group: "flight", currency: currencies[0] || "", depart: "all", hidden: new Set(), table: false };

  function fillSelect(sel, options) {
    sel.replaceChildren();
    options.forEach(function (o) {
      var opt = makeEl("option", o.label);
      opt.value = o.value;
      sel.appendChild(opt);
    });
  }

  fillSelect($("group"), Object.keys(GROUPS).map(function (k) { return { value: k, label: GROUPS[k].label }; }));
  $("group").value = state.group;

  if (currencies.length > 1) {
    fillSelect($("currency"), currencies.map(function (c) { return { value: c, label: c || "(no currency)" }; }));
    $("currency").value = state.currency;
  } else {
    $("currencyWrap").hidden = true;
  }

  var departs = Array.from(new Set(DATA.map(function (r) { return r.depart; }).filter(Boolean))).sort();
  fillSelect($("depart"), [{ value: "all", label: "All dates" }].concat(departs.map(function (d) { return { value: d, label: d }; })));

  $("group").addEventListener("change", function (e) { state.group = e.target.value; state.hidden.clear(); render(); });
  $("currency").addEventListener("change", function (e) { state.currency = e.target.value; state.hidden.clear(); render(); });
  $("depart").addEventListener("change", function (e) { state.depart = e.target.value; render(); });
  $("tableToggle").addEventListener("click", function () {
    state.table = !state.table;
    $("tableToggle").textContent = state.table ? "Hide table" : "Show table";
    $("tableToggle").setAttribute("aria-expanded", String(state.table));
    $("tableWrap").hidden = !state.table;
    render();
  });

  // ---- rendering ---------------------------------------------------------
  function render() {
    var g = GROUPS[state.group];
    var cur = state.currency;
    var base = DATA.filter(function (r) { return currencyOf(r) === cur; });

    // Rank series on the full currency set, not the depart-filtered one, so colours
    // stay with their series when a filter is applied.
    var minBy = new Map();
    base.forEach(function (r) {
      var k = g.key(r), m = minBy.get(k);
      if (m === undefined || r.price < m) minBy.set(k, r.price);
    });
    var ranked = Array.from(minBy.keys()).sort(function (a, b) {
      return minBy.get(a) - minBy.get(b) || a.localeCompare(b);
    });
    var top = ranked.slice(0, MAX_SERIES);
    var extra = ranked.length - top.length;
    var bucketOf = new Map();
    ranked.forEach(function (k, i) { bucketOf.set(k, i < MAX_SERIES ? k : OTHER_ID); });
    var names = top.slice();
    if (extra > 0) names.push(OTHER_ID);
    var labelOf = function (b) { return b === OTHER_ID ? "Other (" + extra + " series)" : b; };
    var colorOf = function (b) {
      return b === OTHER_ID ? "var(--series-other)" : "var(--series-" + (top.indexOf(b) + 1) + ")";
    };

    var points = [];
    base.forEach(function (r) {
      if (state.depart !== "all" && r.depart !== state.depart) return;
      points.push({ r: r, b: bucketOf.get(g.key(r)) });
    });

    renderSummary(base);
    $("note").textContent = extra > 0
      ? "Showing the " + MAX_SERIES + " cheapest series. The other " + extra + " are combined into one grey line."
      : "";
    renderLegend(names, points, colorOf, labelOf, cur);

    var visible = points.filter(function (p) { return !state.hidden.has(p.b); });
    var svg = $("chart");
    svg.replaceChildren();
    var emptyMsg = "";
    if (!base.length) emptyMsg = "No priced offers in this file.";
    else if (!points.length) emptyMsg = "No offers match the current depart date.";
    else if (!visible.length) emptyMsg = "All series are hidden. Tick one in the list.";
    $("empty").textContent = emptyMsg;
    $("empty").hidden = !emptyMsg;
    svg.hidden = !points.length;
    if (points.length) drawChart(points, visible, names, colorOf, labelOf);

    if (state.table) renderTable(visible, labelOf);
  }

  function renderSummary(base) {
    if (!base.length) { $("summary").textContent = "No priced offers in this file."; return; }
    var best = base.reduce(function (a, b) { return b.price < a.price ? b : a; });
    var times = new Set(base.map(function (r) { return r.t; }));
    var minT = base.reduce(function (a, r) { return Math.min(a, r.t); }, Infinity);
    var maxT = base.reduce(function (a, r) { return Math.max(a, r.t); }, -Infinity);
    $("summary").textContent = base.length + " offers across " + times.size + " scrapes, "
      + fmtFull(minT) + " to " + fmtFull(maxT) + ". Lowest: "
      + money(best.price, best.currency, 2) + " (" + (best.flights || "flight number unknown")
      + ", depart " + (best.depart || "?") + ").";
  }

  function renderLegend(names, points, colorOf, labelOf, cur) {
    var stats = new Map(names.map(function (n) { return [n, { n: 0, min: Infinity }]; }));
    points.forEach(function (p) {
      var s = stats.get(p.b);
      s.n++;
      if (p.r.price < s.min) s.min = p.r.price;
    });
    var ul = $("legend");
    ul.replaceChildren();
    names.forEach(function (b) {
      var s = stats.get(b);
      var li = makeEl("li", undefined, ul);
      li.className = state.hidden.has(b) ? "off" : "";
      var label = makeEl("label", undefined, li);
      var cb = makeEl("input", undefined, label);
      cb.type = "checkbox";
      cb.checked = !state.hidden.has(b);
      cb.addEventListener("change", function () {
        if (cb.checked) state.hidden.delete(b); else state.hidden.add(b);
        render();
      });
      var sw = makeEl("span", undefined, label);
      sw.className = "swatch";
      sw.style.background = colorOf(b);
      makeEl("span", labelOf(b), label).className = "name";
      makeEl("span", s.n
        ? s.n + " offers, low " + money(s.min, cur, 0)
        : "no offers at this depart date", label).className = "meta";
    });
  }

  function drawChart(points, visible, names, colorOf, labelOf) {
    var svg = $("chart");
    var t0 = Infinity, t1 = -Infinity, p0 = Infinity, p1 = -Infinity;
    points.forEach(function (p) {
      if (p.r.t < t0) t0 = p.r.t;
      if (p.r.t > t1) t1 = p.r.t;
      if (p.r.price < p0) p0 = p.r.price;
      if (p.r.price > p1) p1 = p.r.price;
    });
    if (t1 - t0 < 3600e3) { t0 -= 1800e3; t1 += 1800e3; }
    var span = (p1 - p0) || Math.max(p1 * 0.1, 1);
    var ystep = niceStep(span, 5);
    p0 = Math.floor(p0 / ystep) * ystep;
    p1 = Math.ceil(p1 / ystep) * ystep;
    if (p1 === p0) p1 = p0 + ystep;

    var sx = function (t) { return M.l + (t - t0) / (t1 - t0) * (W - M.l - M.r); };
    var sy = function (p) { return H - M.b - (p - p0) / (p1 - p0) * (H - M.t - M.b); };

    var gridG = el("g", {}, svg);
    var seriesG = el("g", {}, svg);
    var hitG = el("g", {}, svg);

    var steps = Math.round((p1 - p0) / ystep);
    for (var i = 0; i <= steps; i++) {
      var v = p0 + i * ystep;
      var y = sy(v);
      el("line", { x1: M.l, x2: W - M.r, y1: y, y2: y, class: "grid" }, gridG);
      var yl = el("text", { x: M.l - 8, y: y, class: "tick", "text-anchor": "end", "dominant-baseline": "middle" }, gridG);
      yl.textContent = money(v, state.currency, 0);
    }
    el("line", { x1: M.l, x2: W - M.r, y1: H - M.b, y2: H - M.b, class: "axis" }, gridG);
    for (var j = 0; j <= 4; j++) {
      var t = t0 + j * (t1 - t0) / 4;
      var xl = el("text", {
        x: sx(t), y: H - M.b + 18, class: "tick",
        "text-anchor": j === 0 ? "start" : j === 4 ? "end" : "middle"
      }, gridG);
      xl.textContent = fmtTick(t);
    }

    names.forEach(function (b) {
      if (state.hidden.has(b)) return;
      var pts = visible.filter(function (p) { return p.b === b; });
      if (!pts.length) return;
      var color = colorOf(b);

      var byT = new Map();
      pts.forEach(function (p) {
        var m = byT.get(p.r.t);
        if (m === undefined || p.r.price < m) byT.set(p.r.t, p.r.price);
      });
      var line = Array.from(byT.entries()).sort(function (a, c) { return a[0] - c[0]; });
      if (line.length > 1) {
        var d = line.map(function (q, idx) {
          return (idx ? "L" : "M") + sx(q[0]).toFixed(1) + " " + sy(q[1]).toFixed(1);
        }).join(" ");
        el("path", { d: d, class: "series-line" }, seriesG).style.stroke = color;
      }

      pts.forEach(function (p) {
        var cx = sx(p.r.t), cy = sy(p.r.price);
        el("circle", { cx: cx, cy: cy, r: 4, class: "dot" }, seriesG).style.fill = color;
        var hit = el("circle", { cx: cx, cy: cy, r: 9, class: "hit" }, hitG);
        var title = el("title", {}, hit);
        title.textContent = tooltip(p.r, labelOf(b));
        if (p.r.buy_url) {
          hit.addEventListener("click", function () { window.open(p.r.buy_url, "_blank", "noopener"); });
        }
      });
    });
  }

  function tooltip(r, label) {
    return [
      label + " — " + money(r.price, r.currency, 2),
      fmtFull(r.t),
      [r.vendor, r.flights].filter(Boolean).join(" · "),
      r.airlines,
      "Depart " + (r.depart || "?") + (r.out_time ? " at " + r.out_time : "")
        + (r.ret ? " · return " + r.ret + (r.ret_time ? " at " + r.ret_time : "") : " · one-way"),
      [stopsLabel(r.stops), fmtDur(r.duration)].filter(Boolean).join(" · "),
      r.buy_url ? "Click the dot to open the booking link" : ""
    ].filter(Boolean).join("  |  ");
  }

  function renderTable(visible, labelOf) {
    var head = $("tableHead"), body = $("tableBody");
    head.replaceChildren();
    body.replaceChildren();
    var cols = ["Scraped", "Price", "Series", "Flights", "Airlines", "Vendor", "Route", "Depart", "Return", "Stops", "Duration"];
    var tr = makeEl("tr", undefined, head);
    cols.forEach(function (c) { makeEl("th", c, tr); });
    visible.slice().sort(function (a, b) { return b.r.t - a.r.t || a.r.price - b.r.price; }).forEach(function (p) {
      var r = p.r;
      var row = makeEl("tr", undefined, body);
      makeEl("td", fmtFull(r.t), row);
      makeEl("td", money(r.price, r.currency, 2), row).className = "num";
      makeEl("td", labelOf(p.b), row);
      makeEl("td", r.flights, row);
      makeEl("td", r.airlines, row);
      makeEl("td", r.vendor, row);
      makeEl("td", r.origin && r.destination ? r.origin + " → " + r.destination : "", row);
      makeEl("td", r.depart, row);
      makeEl("td", r.ret || "one-way", row);
      makeEl("td", stopsLabel(r.stops), row);
      makeEl("td", fmtDur(r.duration), row);
    });
  }

  render();
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
