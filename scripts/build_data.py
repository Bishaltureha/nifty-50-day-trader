#!/usr/bin/env python3
"""Build data.json for the scanner page: the NIFTY 50 index and its 50 stocks.

Reuses scanner/scan.py (the trader-scanner tool, set to NSE / IST) for the
Yahoo fetch, the IST calendar, complete daily closes and the setup rules, so
the page shows exactly what `scan.py daily` would find. Standard library only.

    python3 scripts/build_data.py            # -> data.json
"""
import datetime
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("scan", os.path.join(ROOT, "scanner", "scan.py"))
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)

INDEX = ("^NSEI", "NIFTY 50")
KEEP = 90         # daily closes per symbol on the page
OR_MINUTES = 15   # opening range 9:15-9:30

# Strength score, 0-100. Every point is listed on the share's page, so the
# ranking can be checked by hand. It measures how strongly a chart is going up;
# it is not a forecast.
SETUP_POINTS = {"orb": 25, "breakout_20d": 25, "bull_flag": 20, "reclaim_ma20": 15, "oversold_bounce": 10}


def names():
    out = {}
    with open(os.path.join(ROOT, "scanner", "nifty50.txt"), encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                sym, name = ln.split("|", 1)
                out[sym] = name
    return out


def r2(v):
    return None if v is None else round(v, 2)


def item(sym, name, series, now_utc):
    bars = scan.daily_closes(series, now_utc)
    if len(bars) < scan.MIN_DAILY_BARS:
        return None
    closes = [c for _, c in bars]
    setups, nums = scan.find_setups(closes)
    meta = series.get("meta") or {}
    last_d, last = bars[-1]
    live = None
    t, p = meta.get("regularMarketTime"), meta.get("regularMarketPrice")
    if t and p is not None:
        local = scan.ts_to_eastern(t)
        if local.date() > last_d:   # a newer price than the last complete daily bar
            live = {"price": r2(p), "time": local.strftime("%Y-%m-%d %H:%M"),
                    "change_pct": r2((p / last - 1) * 100)}
    tail = bars[-KEEP:]
    return {
        "sym": sym, "name": name,
        "dates": [d.isoformat() for d, _ in tail], "closes": [r2(c) for _, c in tail],
        "last": r2(last), "last_date": last_d.isoformat(),
        "change_pct": r2((last / closes[-2] - 1) * 100),
        "change_5d": r2((last / closes[-6] - 1) * 100), "change_20d": r2((last / closes[-21] - 1) * 100),
        "high_6m": r2(max(closes[-125:])), "low_6m": r2(min(closes[-125:])),
        "volume": meta.get("regularMarketVolume"),
        "setups": setups, "rsi": r2(nums["rsi"]), "rsi_prev": r2(nums["rsi_prev"]),
        "high20": r2(nums["high20"]), "ma20": r2(nums["ma20"]),
        "run_pct": r2(nums["run_pct"]), "range5_pct": r2(nums["range5_pct"]),
        "live": live,
    }


def strength(it):
    """-> (score 0-100, [(points, reason)])."""
    parts = []
    for k in it["setups"] + (["orb"] if it.get("orb") else []):
        if k in SETUP_POINTS:
            parts.append((SETUP_POINTS[k], k))
    if it["last"] > it["ma20"]:
        parts.append((15, "above_ma20"))
    mom = max(-10.0, min(10.0, it["change_20d"]))
    if mom:
        parts.append((round(mom, 1), "change_20d"))
    r = it["rsi"]
    if r is not None and 50 <= r <= 70:
        parts.append((15, "rsi_healthy"))
    elif r is not None and 40 <= r < 50:
        parts.append((5, "rsi_neutral"))
    elif r is not None and r > 70:
        parts.append((5, "rsi_hot"))
    live = it.get("live")
    if live and live["change_pct"] > 0:
        parts.append((round(min(10.0, live["change_pct"] * 4), 1), "today_up"))
    score = max(0, min(100, round(sum(p for p, _ in parts))))
    return score, [[p, k] for p, k in parts]


def orb_today(syms, now):
    """Opening-range breakouts so far today with scan.py's own rules (5-minute bars, a CLOSE
    above the 9:15-9:30 high, confirmed on real highs and lows). -> ({sym: hit}, status text)."""
    local = scan.to_eastern(now)
    day = local.date()
    sess = scan.session(day)
    if not sess:
        return {}, "closed"
    close_dt = datetime.datetime.combine(day, sess[1])
    at = None if local < close_dt else sess[1]
    try:
        day, cutoff = scan.orb_window(now, at, OR_MINUTES, None)
    except scan.ScanError:
        return {}, "pre-open" if local < close_dt else "closed"
    stage1, _ = scan.fetch_spark(syms, "1d", "5m", scan.PAUSE)

    def confirm(sym):
        scan.SLEEP(scan.PAUSE)
        return scan.parse_chart(scan.yahoo_get(scan.chart_path(sym, "1d", "5m")), sym)
    hits, counts, _ = scan.scan_orb(stage1, syms, day, cutoff, OR_MINUTES, confirm=confirm)
    return ({h["symbol"]: {k: h[k] for k in ("at", "price", "or_high", "or_low", "pct_over")} for h in hits},
            "%s %s" % (day.isoformat(), cutoff.strftime("%H:%M")))


def main():
    now = scan.parse_now(None)
    nm = names()
    syms = [INDEX[0]] + list(nm)
    data, failed = scan.fetch_spark(syms, "6mo", "1d", scan.PAUSE)
    try:
        orb, orb_as_of = orb_today(syms, now)
    except Exception as e:   # the daily scan still ships if the intraday fetch fails
        print("orb scan skipped: %s" % e, file=sys.stderr)
        orb, orb_as_of = {}, "unavailable"
    out = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "generated_ist": scan.to_eastern(now).strftime("%Y-%m-%d %H:%M"),
           "orb_as_of": orb_as_of, "index": None, "stocks": [], "failed": sorted(failed)}
    for sym in syms:
        if sym not in data:
            continue
        it = item(sym, INDEX[1] if sym == INDEX[0] else nm[sym], data[sym], now)
        if it is None:
            out["failed"].append(sym)
            continue
        it["orb"] = orb.get(sym)
        it["strength"], it["strength_parts"] = strength(it)
        if sym == INDEX[0]:
            out["index"] = it
        else:
            out["stocks"].append(it)
    if out["index"] is None or len(out["stocks"]) < 40:
        sys.exit("too little data from Yahoo (%d stocks, failed: %s): data.json left as it was"
                 % (len(out["stocks"]), ", ".join(out["failed"])))
    out["as_of"] = max(s["last_date"] for s in out["stocks"])
    with open(os.path.join(ROOT, "data.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    hits = [s["sym"] for s in out["stocks"] if s["setups"]]
    print("data.json: index + %d stocks, closes through %s, %d with a setup, failed: %s"
          % (len(out["stocks"]), out["as_of"], len(hits), ", ".join(out["failed"]) or "none"))


if __name__ == "__main__":
    main()
