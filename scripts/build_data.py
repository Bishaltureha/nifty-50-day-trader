#!/usr/bin/env python3
"""Build data.json for the scanner page: the NIFTY 50 index and its 50 stocks.

Reuses scanner/scan.py (the trader-scanner tool, set to NSE / IST) for the
Yahoo fetch, the IST calendar, complete daily closes and the long-side setup
rules, so the long side shows exactly what `scan.py daily` would find. scan.py
is long-only; the short side here mirrors each of its rules (a 20-day low
instead of a 20-day high, a close below the opening range instead of above, and
so on). Standard library only.

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
EMA_FAST, EMA_SLOW = 8, 34   # the trader's own EMAs

# Strength scores, 0-100, one for each side. Every point is listed on the
# share's page, so the ranking can be checked by hand. A score measures how
# strongly a chart is moving that way; it is not a forecast.
LONG_POINTS = {"orb": 25, "breakout_20d": 25, "bull_flag": 20, "reclaim_ma20": 15, "oversold_bounce": 10}
SHORT_POINTS = {"orb_down": 25, "breakdown_20d": 25, "bear_flag": 20, "lose_ma20": 15, "overbought_drop": 10}


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


def find_short_setups(c):
    """scan.find_setups turned upside down. closes (oldest first, >= 30) -> (setup keys, numbers)."""
    last = c[-1]
    low20 = min(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r, r_prev = scan.rsi(c), scan.rsi(c[:-1])
    tags = []
    if last < low20:
        tags.append("breakdown_20d")
    if run < -0.10 and rng5 < 0.05:
        tags.append("bear_flag")
    if c[-2] > ma20_prev and last < ma20:
        tags.append("lose_ma20")
    if r is not None and r > 70:
        tags.append("overbought")
    if r_prev is not None and r_prev > 70 and last < c[-2]:
        tags.append("overbought_drop")
    return tags, {"low20": low20}


def fill_session_close(series, now_utc):
    """Yahoo sometimes leaves a finished day's daily close empty for hours. When the day's
    session is over and Yahoo's quote is from that day's closing minutes, use the quote as the
    close. -> the date filled in, or None."""
    t, c = series.get("t") or [], series.get("c") or []
    meta = series.get("meta") or {}
    qt, qp = meta.get("regularMarketTime"), meta.get("regularMarketPrice")
    if not t or c[-1] is not None or not qt or qp is None:
        return None
    day = scan.ts_to_eastern(t[-1]).date()
    sess = scan.session(day)
    quote = scan.ts_to_eastern(qt)
    if not sess or quote.date() != day or quote.time() < datetime.time(15, 25):
        return None
    if scan.to_eastern(now_utc) < datetime.datetime.combine(day, sess[1]):
        return None
    c[-1] = qp
    for k, mk in (("h", "regularMarketDayHigh"), ("l", "regularMarketDayLow")):
        if series.get(k) and series[k][-1] is None and meta.get(mk) is not None:
            series[k][-1] = meta[mk]
    return day.isoformat()


def fill_today_live(series, now_utc):
    """In market hours Yahoo's daily bar for today has no close yet. Use the latest quote as
    today's close so far, so setups and patterns read today's move (they are marked as live
    and settle at 15:30). -> "HH:MM" of the quote, or None."""
    t, c = series.get("t") or [], series.get("c") or []
    meta = series.get("meta") or {}
    qt, qp = meta.get("regularMarketTime"), meta.get("regularMarketPrice")
    now = scan.to_eastern(now_utc)
    sess = scan.session(now.date())
    if not t or not qt or qp is None or not sess or now >= datetime.datetime.combine(now.date(), sess[1]):
        return None
    quote = scan.ts_to_eastern(qt)
    if scan.ts_to_eastern(t[-1]).date() != now.date() or quote.date() != now.date():
        return None
    c[-1] = qp
    for k, mk, pick in (("h", "regularMarketDayHigh", max), ("l", "regularMarketDayLow", min)):
        if series.get(k):
            vals = [v for v in (series[k][-1], meta.get(mk), qp) if v is not None]
            series[k][-1] = pick(vals)
    if series.get("o") and series["o"][-1] is None:
        series["o"][-1] = qp
    return quote.strftime("%H:%M")


def daily_clock(now_utc):
    """The clock daily bars are read at: just after today's close while the market is open,
    so today's bar (filled by fill_today_live) counts; otherwise the real clock."""
    now = scan.to_eastern(now_utc)
    sess = scan.session(now.date())
    if sess and now < datetime.datetime.combine(now.date(), sess[1]):
        return scan.eastern_to_utc(datetime.datetime.combine(now.date(), sess[1])) + datetime.timedelta(minutes=1)
    return now_utc


def item(sym, name, series, now_utc):
    filled = fill_session_close(series, now_utc)
    provisional = fill_today_live(series, now_utc)
    bars = scan.daily_closes(series, daily_clock(now_utc))
    if len(bars) < scan.MIN_DAILY_BARS:
        return None
    closes = [c for _, c in bars]
    setups, nums = scan.find_setups(closes)
    short_setups, snums = find_short_setups(closes)
    meta = series.get("meta") or {}
    last_d, last = bars[-1]
    live = None
    t, p = meta.get("regularMarketTime"), meta.get("regularMarketPrice")
    if t and p is not None:
        local = scan.ts_to_eastern(t)
        if local.date() > last_d:   # a newer price than the last daily bar
            live = {"price": r2(p), "time": local.strftime("%Y-%m-%d %H:%M"),
                    "change_pct": r2((p / last - 1) * 100)}
    tail = bars[-KEEP:]
    return {
        "sym": sym, "name": name,
        "dates": [d.isoformat() for d, _ in tail], "closes": [r2(c) for _, c in tail],
        # over the whole history, then cut, so the first values on the page are settled
        "ema_fast": [r2(v) for v in ema(closes, EMA_FAST)[-len(tail):]],
        "ema_slow": [r2(v) for v in ema(closes, EMA_SLOW)[-len(tail):]],
        "last": r2(last), "last_date": last_d.isoformat(),
        "change_pct": r2((last / closes[-2] - 1) * 100),
        "change_5d": r2((last / closes[-6] - 1) * 100), "change_20d": r2((last / closes[-21] - 1) * 100),
        "high_6m": r2(max(closes[-125:])), "low_6m": r2(min(closes[-125:])),
        "volume": meta.get("regularMarketVolume"),
        "setups": setups, "short_setups": short_setups,
        "rsi": r2(nums["rsi"]), "rsi_prev": r2(nums["rsi_prev"]),
        "high20": r2(nums["high20"]), "low20": r2(snums["low20"]), "ma20": r2(nums["ma20"]),
        "run_pct": r2(nums["run_pct"]), "range5_pct": r2(nums["range5_pct"]),
        "live": live, "close_from_quote": filled,
        # set in market hours: the last bar is today's, its close is the price at this time
        "provisional": provisional, "prev_close": r2(closes[-2]),
    }


def today_move(it):
    """Today's % move: from a newer quote, or from today's live daily bar; None if neither."""
    if it.get("live"):
        return it["live"]["change_pct"]
    return it["change_pct"] if it.get("provisional") else None


def long_strength(it):
    """-> (score 0-100, [[points, reason]])."""
    parts = []
    for k in it["setups"] + (["orb"] if it.get("orb") else []):
        if k in LONG_POINTS:
            parts.append((LONG_POINTS[k], k))
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
    today = today_move(it)
    if today is not None and today > 0:
        parts.append((round(min(10.0, today * 4), 1), "today_up"))
    parts += pattern_points(it.get("patterns") or {}, "long")
    return max(0, min(100, round(sum(p for p, _ in parts)))), [[p, k] for p, k in parts]


def short_strength(it):
    """The same score for the way down. -> (score 0-100, [[points, reason]])."""
    parts = []
    for k in it["short_setups"] + (["orb_down"] if it.get("orb_short") else []):
        if k in SHORT_POINTS:
            parts.append((SHORT_POINTS[k], k))
    if it["last"] < it["ma20"]:
        parts.append((15, "below_ma20"))
    mom = max(-10.0, min(10.0, -it["change_20d"]))
    if mom:
        parts.append((round(mom, 1), "fall_20d"))
    r = it["rsi"]
    if r is not None and 30 <= r <= 50:
        parts.append((15, "rsi_weak"))
    elif r is not None and 50 < r <= 60:
        parts.append((5, "rsi_neutral_s"))
    elif r is not None and r < 30:
        parts.append((5, "rsi_cold"))
    today = today_move(it)
    if today is not None and today < 0:
        parts.append((round(min(10.0, -today * 4), 1), "today_down"))
    parts += pattern_points(it.get("patterns") or {}, "short")
    return max(0, min(100, round(sum(p for p, _ in parts)))), [[p, k] for p, k in parts]


# ---------------------------------------------------------------- candle patterns, three timeframes

# key -> side ("long", "short" or "" for neutral). The page explains each one.
PATTERNS = {
    "bull_engulf": "long", "bear_engulf": "short", "hammer": "long", "shooting_star": "short",
    "morning_star": "long", "evening_star": "short", "ema_up": "long", "ema_down": "short",
    "high20": "long", "low20": "short", "doji": "", "inside_bar": "", "nr7": "",
}
TF_POINTS = {"1d": 8, "1h": 5, "15m": 3}   # the most a timeframe's patterns add to a score


def ohlc_bars(series, keep):
    """Chart series -> complete OHLC bars [{t, o, h, l, c}] for which `keep(local_start)` is true."""
    out = []
    for i, ts in enumerate(series["t"]):
        vals = [series[k][i] if series.get(k) else None for k in "ohlc"]
        if None in vals:
            continue
        local = scan.ts_to_eastern(ts)
        if keep(local):
            o, h, l, c = (float(v) for v in vals)
            out.append({"t": local, "o": o, "h": h, "l": l, "c": c})
    return out


def hourly(bars15):
    """15-minute bars -> NSE hourly candles (9:15-10:15, 10:15-11:15, ..., 15:15-15:30)."""
    groups = {}
    for b in bars15:
        mins = b["t"].hour * 60 + b["t"].minute - (9 * 60 + 15)
        key = (b["t"].date(), mins // 60)
        groups.setdefault(key, []).append(b)
    out = []
    for key in sorted(groups):
        g = groups[key]
        out.append({"t": g[0]["t"], "o": g[0]["o"], "h": max(b["h"] for b in g), "l": min(b["l"] for b in g),
                    "c": g[-1]["c"], "n": len(g)})
    return out


def ema(vals, n):
    k, e, out = 2.0 / (n + 1), vals[0], []
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def ema_state(bars):
    """EMA 8 / 34 on the last complete candle: "up" when close > EMA 8 > EMA 34, "down" when
    close < EMA 8 < EMA 34, else "mixed"."""
    c = [b["c"] for b in bars]
    if len(c) < EMA_SLOW:
        return None
    f, s, last = ema(c, EMA_FAST)[-1], ema(c, EMA_SLOW)[-1], c[-1]
    st = "up" if last > f > s else "down" if last < f < s else "mixed"
    return {"fast": round(f, 2), "slow": round(s, 2), "st": st}


def candle_patterns(bars, daily=False):
    """Patterns on the LAST complete bar. bars oldest first, >= 25. -> [keys]."""
    if len(bars) < 25:
        return []
    b, p, q = bars[-1], bars[-2], bars[-3]
    c = [x["c"] for x in bars]
    body, rng = abs(b["c"] - b["o"]), b["h"] - b["l"]
    if rng <= 0:
        return []
    upper, lower = b["h"] - max(b["o"], b["c"]), min(b["o"], b["c"]) - b["l"]
    fell, rose = c[-2] < c[-7], c[-2] > c[-7]   # the five bars before this one
    out = []
    if fell and p["c"] < p["o"] and b["c"] > b["o"] and b["o"] <= p["c"] and b["c"] >= p["o"]:
        out.append("bull_engulf")
    if rose and p["c"] > p["o"] and b["c"] < b["o"] and b["o"] >= p["c"] and b["c"] <= p["o"]:
        out.append("bear_engulf")
    if fell and body > 0 and lower >= 2 * body and upper <= 0.3 * rng:
        out.append("hammer")
    if rose and body > 0 and upper >= 2 * body and lower <= 0.3 * rng:
        out.append("shooting_star")
    qb, pb = abs(q["c"] - q["o"]), abs(p["c"] - p["o"])
    qr, pr = q["h"] - q["l"], p["h"] - p["l"]
    if qr > 0 and pr > 0:
        mid = (q["o"] + q["c"]) / 2
        if c[-3] < c[-8] and q["c"] < q["o"] and qb >= 0.5 * qr and pb <= 0.3 * pr and b["c"] > b["o"] and b["c"] > mid:
            out.append("morning_star")
        if c[-3] > c[-8] and q["c"] > q["o"] and qb >= 0.5 * qr and pb <= 0.3 * pr and b["c"] < b["o"] and b["c"] < mid:
            out.append("evening_star")
    ef, es = ema(c, EMA_FAST), ema(c, EMA_SLOW)
    if ef[-2] <= es[-2] and ef[-1] > es[-1]:
        out.append("ema_up")
    if ef[-2] >= es[-2] and ef[-1] < es[-1]:
        out.append("ema_down")
    if not daily and c[-1] > max(c[-21:-1]):
        out.append("high20")
    if not daily and c[-1] < min(c[-21:-1]):
        out.append("low20")
    if body <= 0.1 * rng:
        out.append("doji")
    if b["h"] < p["h"] and b["l"] > p["l"]:
        out.append("inside_bar")
    # NR7 only on daily candles: intraday ranges shrink every afternoon, so it would say little
    if daily and rng <= min(x["h"] - x["l"] for x in bars[-7:]):
        out.append("nr7")
    return out


# Chart patterns, from swing points. A swing high is a bar whose high is the highest of the
# SWING bars on each side; a swing low likewise. Only patterns whose last swing is recent count.
CHART = {"double_bottom": "long", "double_top": "short", "inv_head_shoulders": "long", "head_shoulders": "short",
         "asc_triangle": "long", "desc_triangle": "short", "sym_triangle": "",
         "cup_handle": "long", "inv_cup_handle": "short", "rounding_bottom": "long", "rounding_top": "short",
         "falling_wedge": "long", "rising_wedge": "short"}
CUP_DEPTH = {True: (0.08, 0.40), False: (0.02, 0.15)}   # daily / hourly: how deep a cup may be
SWING = 3
RECENT = 20    # the pattern's last swing must be within this many bars of the end


def swings(bars):
    """-> (highs [(i, price)], lows [(i, price)]) of confirmed swing points."""
    hi, lo = [], []
    for i in range(SWING, len(bars) - SWING):
        win = bars[i - SWING:i + SWING + 1]
        if bars[i]["h"] == max(b["h"] for b in win):
            hi.append((i, bars[i]["h"]))
        if bars[i]["l"] == min(b["l"] for b in win):
            lo.append((i, bars[i]["l"]))
    return hi, lo


def near(a, b, pct):
    return abs(a - b) / min(a, b) * 100 <= pct


def invert(bars):
    """Turn a chart upside down (1 / price), so a rule written for the way up finds its mirror."""
    return [{"t": b["t"], "o": 1 / b["o"], "h": 1 / b["l"], "l": 1 / b["h"], "c": 1 / b["c"]} for b in bars]


def cup(bars, dmin, dmax):
    """Cup and handle, or a rounding bottom still on its way up to the rim. -> pattern or None.
    Left rim: a swing high. Cup: a U, its low in the middle 60% of the width, at least 15 bars
    wide and dmin-dmax deep, with some bars near the bottom (a U, not a V). Right rim: the first
    bar back within 3% of the left rim. Handle: the bars after the right rim until a close above
    the rim; it may give back at most half the cup and last at most half its width."""
    n, last = len(bars), bars[-1]["c"]
    hi, _ = swings(bars)
    best = None
    for a, L in hi:
        if a < n - 150 or a > n - 15:
            continue
        bi = min(range(a + 1, n), key=lambda i: bars[i]["l"])
        B = bars[bi]["l"]
        if bi >= n - 3 or not dmin <= (L - B) / L <= dmax:
            continue
        ri = next((i for i in range(bi + 1, n) if bars[i]["h"] >= L * 0.97), None)
        end = ri if ri is not None else n - 1
        width = end - a
        low_third = B + (L - B) / 3
        if width < 15 or not 0.2 <= (bi - a) / width <= 0.8 \
                or sum(1 for b in bars[a:end + 1] if b["c"] <= low_third) < max(3, 0.15 * width):
            continue
        if ri is None:   # still climbing the right side
            if (last - B) / (L - B) >= 0.5:
                cand = {"k": "rounding_bottom", "st": "forming", "lvl": L, "_w": width}
            else:
                continue
        else:
            if n - 1 - ri > 40:
                continue
            rim = max(L, bars[ri]["h"])
            k = next((i for i in range(ri + 1, n) if bars[i]["c"] > rim), None)
            handle = bars[ri + 1:k if k is not None else n]
            if len(handle) > width / 2 or (handle and min(b["l"] for b in handle) < rim - 0.5 * (rim - B)):
                continue
            if k is not None and (len(handle) < 3 or k < n - 20 or last <= rim):
                continue   # no real handle, an old breakout, or a failed one (back below the rim)
            cand = {"k": "cup_handle", "st": "confirmed" if k is not None else "forming", "lvl": rim, "_w": width}
        if best is None or cand["_w"] > best["_w"]:
            best = cand
    if best:
        del best["_w"]
    return best


def rising_wedge(bars):
    """The last 2-3 swing highs and lows (within 50 bars) all rising, the lows faster than the
    highs, so the lines meet. -> pattern or None. Breaking the last swing low confirms it."""
    n, last = len(bars), bars[-1]["c"]
    hi, lo = swings(bars)
    rh = [x for x in hi if x[0] >= n - 50][-3:]
    rl = [x for x in lo if x[0] >= n - 50][-3:]
    if len(rh) < 2 or len(rl) < 2 or max(rh[-1][0], rl[-1][0]) < n - 1 - RECENT:
        return None
    rising = lambda pts: all(b[1] > a[1] * 1.003 for a, b in zip(pts, pts[1:]))
    slope = lambda pts: (pts[-1][1] / pts[0][1] - 1) / max(1, pts[-1][0] - pts[0][0])
    if not (rising(rh) and rising(rl)) or slope(rl) <= slope(rh) * 1.2:
        return None
    return {"k": "rising_wedge", "st": "confirmed" if last < rl[-1][1] else "forming", "lvl": rl[-1][1]}


def mirrored(p, key):
    """A pattern found on the inverted chart, back in real prices under its mirror's name."""
    return p and {"k": key, "st": p["st"], "lvl": 1 / p["lvl"]}


def chart_patterns(bars, daily=True):
    """-> [{"k": key, "st": "forming"|"confirmed", "lvl": the level that confirms it}]."""
    if len(bars) < 40:
        return []
    n, last = len(bars), bars[-1]["c"]
    hi, lo = swings(bars)
    out = []

    def low_between(i, j):
        return min(b["l"] for b in bars[i:j + 1])

    def high_between(i, j):
        return max(b["h"] for b in bars[i:j + 1])
    # double top / bottom: the last two swings at about the same price, a real dip or rise between.
    # A top needs the newest swing to be a high, a bottom the newest swing to be a low, so a chart
    # never shows both at once.
    newest_is_high = bool(hi) and (not lo or hi[-1][0] > lo[-1][0])
    if len(hi) >= 2 and newest_is_high:
        (i1, p1), (i2, p2) = hi[-2], hi[-1]
        neck = low_between(i1, i2)
        if i2 - i1 >= 5 and n - 1 - i2 <= RECENT and near(p1, p2, 1.5) and neck <= min(p1, p2) * 0.97 and last < max(p1, p2):
            out.append({"k": "double_top", "st": "confirmed" if last < neck else "forming", "lvl": round(neck, 2)})
    if len(lo) >= 2 and not newest_is_high:
        (i1, p1), (i2, p2) = lo[-2], lo[-1]
        neck = high_between(i1, i2)
        if i2 - i1 >= 5 and n - 1 - i2 <= RECENT and near(p1, p2, 1.5) and neck >= max(p1, p2) * 1.03 and last > min(p1, p2):
            out.append({"k": "double_bottom", "st": "confirmed" if last > neck else "forming", "lvl": round(neck, 2)})
    # head and shoulders: three swing highs, the middle one clearly the highest, the shoulders level
    if len(hi) >= 3:
        (a, l), (b, h), (c, r) = hi[-3], hi[-2], hi[-1]
        if h >= max(l, r) * 1.02 and near(l, r, 3) and n - 1 - c <= RECENT:
            neck = min(low_between(a, b), low_between(b, c))
            if last < r:
                out.append({"k": "head_shoulders", "st": "confirmed" if last < neck else "forming", "lvl": round(neck, 2)})
    if len(lo) >= 3:
        (a, l), (b, h), (c, r) = lo[-3], lo[-2], lo[-1]
        if h <= min(l, r) * 0.98 and near(l, r, 3) and n - 1 - c <= RECENT:
            neck = max(high_between(a, b), high_between(b, c))
            if last > r:
                out.append({"k": "inv_head_shoulders", "st": "confirmed" if last > neck else "forming", "lvl": round(neck, 2)})
    # triangles: the last three swing highs and lows within the last 40 bars
    rh = [p for i, p in hi if i >= n - 40][-3:]
    rl = [p for i, p in lo if i >= n - 40][-3:]
    if len(rh) >= 2 and len(rl) >= 2 and max(i for i, _ in hi[-1:] + lo[-1:]) >= n - 1 - RECENT:
        flat_top = max(rh) / min(rh) - 1 <= 0.01
        flat_bottom = max(rl) / min(rl) - 1 <= 0.01
        rising_lows = all(b > a * 1.005 for a, b in zip(rl, rl[1:]))
        falling_highs = all(b < a * 0.995 for a, b in zip(rh, rh[1:]))
        if flat_top and rising_lows:
            top = max(rh)
            out.append({"k": "asc_triangle", "st": "confirmed" if last > top else "forming", "lvl": round(top, 2)})
        elif flat_bottom and falling_highs:
            floor = min(rl)
            out.append({"k": "desc_triangle", "st": "confirmed" if last < floor else "forming", "lvl": round(floor, 2)})
        elif falling_highs and rising_lows:
            st = "up" if last > rh[-1] else "down" if last < rl[-1] else "forming"
            out.append({"k": "sym_triangle", "st": st, "lvl": round(rh[-1] if st != "down" else rl[-1], 2)})
    # cup and handle family and wedges; the short side is the long rule on the upside-down chart
    dmin, dmax = CUP_DEPTH[daily]
    up = cup(bars, dmin, dmax)
    down = cup(invert(bars), dmin, dmax)
    names = {"cup_handle": "inv_cup_handle", "rounding_bottom": "rounding_top"}
    down = mirrored(down, names[down["k"]]) if down else None
    if up and down:   # both ways at once: keep a confirmed one over a forming one, else neither
        up, down = (up, None) if up["st"] > down["st"] else (None, down) if down["st"] > up["st"] else (None, None)
    out += [x for x in (up, down) if x]
    out += [x for x in (rising_wedge(bars), mirrored(rising_wedge(invert(bars)), "falling_wedge")) if x]
    for x in out:
        x["lvl"] = round(x["lvl"], 2)
    return out


def levels(bars, last):
    """Nearest resistance above and support below `last`, from swing points that cluster
    within 1%. -> {"res": [price, touches] | None, "sup": [price, touches] | None}"""
    hi, lo = swings(bars)
    pts = sorted(p for _, p in hi + lo)
    clusters = []
    for p in pts:   # a point joins a cluster only if it is within 1% of the cluster's average
        if clusters and near(sum(clusters[-1]) / len(clusters[-1]), p, 1.0):
            clusters[-1].append(p)
        else:
            clusters.append([p])
    lv = [(sum(c) / len(c), len(c)) for c in clusters]
    above = [x for x in lv if x[0] > last * 1.002]
    below = [x for x in lv if x[0] < last * 0.998]
    res = min(above, key=lambda x: x[0]) if above else None
    sup = max(below, key=lambda x: x[0]) if below else None
    return {"res": [round(res[0], 2), res[1]] if res else None, "sup": [round(sup[0], 2), sup[1]] if sup else None}


def patterns_for(daily_series, m15_series, now_utc):
    """-> {"1d"|"1h"|"15m": {"at": candle start, "found": [candle keys], "chart": [chart patterns]},
    "levels": nearest daily support / resistance}, on complete candles only."""
    now = scan.to_eastern(now_utc)
    out = {}
    last_daily = {d for d, _ in scan.daily_closes(daily_series, daily_clock(now_utc))}
    d = ohlc_bars(daily_series, lambda t: t.date() in last_daily)
    if d:   # the daily 20-day high / low is already a setup, so candle_patterns leaves it out
        out["1d"] = {"at": d[-1]["t"].strftime("%Y-%m-%d"), "found": candle_patterns(d, daily=True),
                     "chart": chart_patterns(d), "ema": ema_state(d)}
        out["levels"] = levels(d, d[-1]["c"])
    if m15_series:
        b15 = ohlc_bars(m15_series, lambda t: scan.session(t.date()) and t + datetime.timedelta(minutes=15) <= now)
        b15 = [b for b in b15 if datetime.time(9, 15) <= b["t"].time() < datetime.time(15, 30)]
        if b15:
            out["15m"] = {"at": b15[-1]["t"].strftime("%Y-%m-%d %H:%M"), "found": candle_patterns(b15),
                          "ema": ema_state(b15)}
            # full hours only: drops the hour still running and the 15-minute 15:15-15:30 stub,
            # which would always look like the narrowest candle
            h = [x for x in hourly(b15) if x["n"] == 4]
            if h:
                out["1h"] = {"at": h[-1]["t"].strftime("%Y-%m-%d %H:%M"), "found": candle_patterns(h),
                             "chart": chart_patterns(h, daily=False), "ema": ema_state(h)}
    return out


CHART_POINTS = {"1d": (10, 4), "1h": (6, 2)}   # (confirmed, still forming)


def chart_side(cp):
    """The way a chart pattern points; a symmetrical triangle points the way it broke."""
    if cp["k"] == "sym_triangle":
        return {"up": "long", "down": "short"}.get(cp["st"], "")
    return CHART.get(cp["k"], "")


def pattern_points(pats, side):
    """One score line per timeframe with a candle pattern pointing `side`, and one per timeframe
    with a chart pattern pointing `side` (confirmed counts more than forming)."""
    parts = []
    for tf in ("1d", "1h", "15m"):
        found = [k for k in (pats.get(tf) or {}).get("found", []) if PATTERNS.get(k) == side]
        if found:
            parts.append((TF_POINTS[tf], "tf_" + tf))
    for tf, (full, forming) in CHART_POINTS.items():
        mine = [cp for cp in (pats.get(tf) or {}).get("chart", []) if chart_side(cp) == side]
        if mine:
            done = any(cp["st"] in ("confirmed", "up", "down") for cp in mine)
            parts.append((full if done else forming, "chart_" + tf))
    return parts


def orb_low_hit(bars, n_range):
    """scan.orb_hit turned upside down: the first completed bar to CLOSE below the range low."""
    rng = bars[:n_range]
    if any(b["h"] is None or b["l"] is None for b in rng):
        return None
    oh = max(b["h"] for b in rng)
    ol = min(b["l"] for b in rng)
    for k in range(n_range, len(bars)):
        b = bars[k]
        if b["c"] < ol:
            closes_at = b["start"] + datetime.timedelta(minutes=scan.BAR_MIN)
            return {"price": round(b["c"], 2), "at": scan.clock_label(closes_at),
                    "or_high": round(oh, 2), "or_low": round(ol, 2), "pct_under": round((b["c"] / ol - 1) * 100, 2)}
    return None


def orb_today(syms, now):
    """Opening-range breakouts (up) and breakdowns (down) so far today: 5-minute bars, the
    first CLOSE beyond the 9:15-9:30 range, checked on real highs and lows as scan.py does.
    -> ({sym: up hit}, {sym: down hit}, status text)."""
    local = scan.to_eastern(now)
    day = local.date()
    sess = scan.session(day)
    if not sess:
        return {}, {}, "closed"
    close_dt = datetime.datetime.combine(day, sess[1])
    at = None if local < close_dt else sess[1]
    try:
        day, cutoff = scan.orb_window(now, at, OR_MINUTES, None)
    except scan.ScanError:
        return {}, {}, "pre-open" if local < close_dt else "closed"
    n = OR_MINUTES // scan.BAR_MIN
    stage1, _ = scan.fetch_spark(syms, "1d", "5m", scan.PAUSE)
    ups, downs = {}, {}
    for sym in syms:
        s = stage1.get(sym)
        if not s:
            continue
        bars = scan.session_bars(s, day, cutoff)
        if not scan.usable(bars, day, cutoff, n):
            continue
        rng = [b["c"] for b in bars[:n]]
        up = any(b["c"] >= max(rng) for b in bars[n:])     # needed for a close above the range high
        down = any(b["c"] <= min(rng) for b in bars[n:])   # needed for a close below the range low
        if not (up or down):
            continue
        scan.SLEEP(scan.PAUSE)
        try:
            full = scan.session_bars(scan.parse_chart(scan.yahoo_get(scan.chart_path(sym, "1d", "5m")), sym),
                                     day, cutoff)
        except scan.ScanError:
            continue
        if not scan.usable(full, day, cutoff, n):
            continue
        h = scan.orb_hit(full, n) if up else None
        if h:
            ups[sym] = {k: h[k] for k in ("at", "price", "or_high", "or_low", "pct_over")}
        h = orb_low_hit(full, n) if down else None
        if h:
            downs[sym] = h
    return ups, downs, "%s %s" % (day.isoformat(), cutoff.strftime("%H:%M"))


def charts(syms, rng, interval):
    """One v8 chart (with opens, highs and lows) per symbol. -> ({sym: series}, [failed])"""
    data, failed = {}, []
    for k, sym in enumerate(syms):
        if k:
            scan.SLEEP(scan.PAUSE)
        try:
            data[sym] = scan.parse_chart(scan.yahoo_get(scan.chart_path(sym, rng, interval)), sym)
        except scan.ScanError:
            failed.append(sym)
    return data, failed


def main():
    now = scan.parse_now(None)
    nm = names()
    syms = [INDEX[0]] + list(nm)
    data, failed = charts(syms, "6mo", "1d")
    m15, _ = charts(syms, "1mo", "15m")
    try:
        ups, downs, orb_as_of = orb_today(syms, now)
    except Exception as e:   # the daily scan still ships if the intraday fetch fails
        print("orb scan skipped: %s" % e, file=sys.stderr)
        ups, downs, orb_as_of = {}, {}, "unavailable"
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
        it["orb"], it["orb_short"] = ups.get(sym), downs.get(sym)
        it["patterns"] = patterns_for(data[sym], m15.get(sym), now)
        it["strength"], it["strength_parts"] = long_strength(it)
        it["short_strength"], it["short_parts"] = short_strength(it)
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
    nl = len([s for s in out["stocks"] if s["setups"] or s["orb"]])
    ns = len([s for s in out["stocks"] if s["short_setups"] or s["orb_short"]])
    print("data.json: index + %d stocks, closes through %s, %d long and %d short with a setup, "
          "orb %s: %d up / %d down, failed: %s"
          % (len(out["stocks"]), out["as_of"], nl, ns, orb_as_of, len(ups), len(downs),
             ", ".join(out["failed"]) or "none"))


if __name__ == "__main__":
    main()
