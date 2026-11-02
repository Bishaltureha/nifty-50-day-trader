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
KEEP = 90   # daily closes per symbol on the page


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


def main():
    now = scan.parse_now(None)
    nm = names()
    syms = [INDEX[0]] + list(nm)
    data, failed = scan.fetch_spark(syms, "6mo", "1d", scan.PAUSE)
    out = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "generated_ist": scan.to_eastern(now).strftime("%Y-%m-%d %H:%M"),
           "index": None, "stocks": [], "failed": sorted(failed)}
    for sym in syms:
        if sym not in data:
            continue
        it = item(sym, INDEX[1] if sym == INDEX[0] else nm[sym], data[sym], now)
        if it is None:
            out["failed"].append(sym)
        elif sym == INDEX[0]:
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
