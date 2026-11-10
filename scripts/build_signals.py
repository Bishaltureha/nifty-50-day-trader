#!/usr/bin/env python3
"""signals.json: B / S signals on NIFTY 50 5-minute candles, from the same rules as the
"NIFTY Trendlines" TradingView indicator (nifty_trendlines.pine).

Rules, bar by bar, exactly in the indicator's order:
  1. Swing highs / lows: a high (low) with `LEN` lower highs (higher lows) on each side.
  2. Two falling swing highs -> a falling resistance line. Two rising swing lows -> a rising
     support line. Every swing high / low also starts a horizontal level.
  3. Each candle extends the live lines. A CLOSE that crosses a line breaks it.
     A line older than MAX_AGE candles is forgotten.
  4. Falling resistance + rising support both live and still apart = a triangle.
  5. B = a break up through resistance (Triangle BO / Trendline BO) or a swing high (High BO),
     only while Supertrend(10, 3) is up. S = the mirror, only while Supertrend is down.
Only finished candles count: the candle still forming is left out.

    python3 scripts/build_signals.py [OUT]     # default signals.json
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

SYM = "^NSEI"
LEN = 5          # swing size, bars each side
MAX_AGE = 150    # forget a line after this many bars
ST_FACTOR, ST_ATR = 3.0, 10
TREND_FILTER = True
SHOW_DAYS = 3    # sessions written out for the chart
OPEN_T, CLOSE_T = datetime.time(9, 15), datetime.time(15, 30)


def bars_5m(now_ist):
    """Finished 5-minute NSE session candles, oldest first: [(ist datetime, o, h, l, c)]."""
    s = scan.parse_chart(scan.yahoo_get(scan.chart_path(SYM, "1mo", "5m")), SYM)
    out = []
    for i, ts in enumerate(s["t"]):
        o, h, l, c = s["o"][i], s["h"][i], s["l"][i], s["c"][i]
        if None in (o, h, l, c):
            continue
        t = scan.ts_to_eastern(ts)
        if t.second or t.minute % 5 or not (OPEN_T <= t.time() < CLOSE_T):
            continue  # the live tick off the grid, or outside the session
        if t + datetime.timedelta(minutes=5) > now_ist:
            continue  # still forming
        if out and out[-1][0] == t:
            out[-1] = (t, o, h, l, c)
        else:
            out.append((t, o, h, l, c))
    return out


def supertrend(H, L, C, factor, n):
    """TradingView ta.supertrend: -> (values, directions); direction -1 = up, 1 = down."""
    N = len(C)
    tr = [H[0] - L[0]] + [max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1])) for i in range(1, N)]
    atr = [None] * N
    for i in range(N):
        if i == n - 1:
            atr[i] = sum(tr[:n]) / n
        elif i >= n:
            atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n
    st, dr = [None] * N, [None] * N
    up_b = lo_b = None
    for i in range(N):
        if atr[i] is None:
            continue
        src = (H[i] + L[i]) / 2
        ub, lb = src + factor * atr[i], src - factor * atr[i]
        pl, pu = (lo_b or 0.0), (up_b or 0.0)
        lb = lb if (lb > pl or C[i - 1] < pl) else pl
        ub = ub if (ub < pu or C[i - 1] > pu) else pu
        if i == 0 or atr[i - 1] is None:
            d = 1
        elif st[i - 1] == up_b:
            d = -1 if C[i] > ub else 1
        else:
            d = 1 if C[i] < lb else -1
        st[i], dr[i] = (lb if d == -1 else ub), d
        up_b, lo_b = ub, lb
    return st, dr


def pivot(vals, i, ln, high):
    """Is vals[i] a swing high (low) with ln bars on each side? Ties on the right count, as in Pine."""
    if i - ln < 0 or i + ln >= len(vals):
        return False
    v = vals[i]
    left, right = vals[i - ln:i], vals[i + 1:i + ln + 1]
    if high:
        return all(v > x for x in left) and all(v >= x for x in right)
    return all(v < x for x in left) and all(v <= x for x in right)


class Line:
    def __init__(self, kind, x1, y1, x2, y2):
        self.kind, self.x1, self.y1 = kind, x1, y1
        self.slope = 0.0 if x2 == x1 else (y2 - y1) / (x2 - x1)
        self.x2 = x2
        self.broken = False
        self.tri = False

    def at(self, x):
        return self.y1 + self.slope * (x - self.x1)


def run(bars):
    O = [b[1] for b in bars]
    H = [b[2] for b in bars]
    L = [b[3] for b in bars]
    C = [b[4] for b in bars]
    st, dr = supertrend(H, L, C, ST_FACTOR, ST_ATR)
    lines, signals = [], []
    hA = hAi = hB = hBi = lA = lAi = lB = lBi = None
    res = sup = hzH = hzL = None
    res_on = sup_on = hzH_on = hzL_on = tri_on = False
    for i in range(len(bars)):
        p = i - LEN  # a swing is known LEN bars after it
        if p >= 0 and pivot(H, p, LEN, True):
            hA, hAi, hB, hBi = hB, hBi, H[p], p
            if hA is not None and hB < hA:
                res = Line("res", hAi, hA, hBi, hB)
                res.x2 = i
                lines.append(res)
                res_on, tri_on = True, False
            hzH = Line("hzH", hBi, hB, hBi, hB)
            hzH.x2 = i
            lines.append(hzH)
            hzH_on = True
        if p >= 0 and pivot(L, p, LEN, False):
            lA, lAi, lB, lBi = lB, lBi, L[p], p
            if lA is not None and lB > lA:
                sup = Line("sup", lAi, lA, lBi, lB)
                sup.x2 = i
                lines.append(sup)
                sup_on, tri_on = True, False
            hzL = Line("hzL", lBi, lB, lBi, lB)
            hzL.x2 = i
            lines.append(hzL)
            hzL_on = True

        res_brk = sup_brk = hz_up = hz_dn = False
        was_tri = tri_on
        if res_on:
            res.x2 = i
            if i and C[i] > res.at(i) and C[i - 1] <= res.at(i - 1):
                res_brk, res_on, res.broken = True, False, True
            elif i - res.x1 > MAX_AGE:
                res_on = False
        if sup_on:
            sup.x2 = i
            if i and C[i] < sup.at(i) and C[i - 1] >= sup.at(i - 1):
                sup_brk, sup_on, sup.broken = True, False, True
            elif i - sup.x1 > MAX_AGE:
                sup_on = False
        if hzH_on:
            hzH.x2 = i
            if i and C[i] > hzH.y1 and C[i - 1] <= hzH.y1:
                hz_up, hzH_on, hzH.broken = True, False, True
            elif i - hzH.x1 > MAX_AGE:
                hzH_on = False
        if hzL_on:
            hzL.x2 = i
            if i and C[i] < hzL.y1 and C[i - 1] >= hzL.y1:
                hz_dn, hzL_on, hzL.broken = True, False, True
            elif i - hzL.x1 > MAX_AGE:
                hzL_on = False

        if res_on and sup_on and not tri_on and res.at(i) > sup.at(i):
            res.tri = sup.tri = True
            res.tri_with = sup
            tri_on = True
        if not (res_on and sup_on):
            tri_on = False

        up = dr[i] is not None and dr[i] < 0
        ok_up = (not TREND_FILTER) or up
        ok_dn = (not TREND_FILTER) or (dr[i] is not None and not up)
        if ok_up and (res_brk or hz_up):
            kind = "Triangle BO" if res_brk and was_tri else "Trendline BO" if res_brk else "High BO"
            signals.append({"i": i, "side": "B", "kind": kind, "price": round(C[i], 2)})
        if ok_dn and (sup_brk or hz_dn):
            kind = "Triangle BD" if sup_brk and was_tri else "Trendline BD" if sup_brk else "Low BD"
            signals.append({"i": i, "side": "S", "kind": kind, "price": round(C[i], 2)})
    return st, dr, lines, signals


def main(out=os.path.join(ROOT, "signals.json")):
    now = scan.parse_now(None)
    now_ist = scan.to_eastern(now)
    try:
        bars = bars_5m(now_ist)
    except scan.ScanError as e:
        print("signals: %s" % e, file=sys.stderr)
        return 1
    if len(bars) < 60:
        print("signals: only %d candles from Yahoo, signals.json left as it was" % len(bars), file=sys.stderr)
        return 1
    st, dr, lines, signals = run(bars)

    days = sorted({b[0].date() for b in bars})[-SHOW_DAYS:]
    start = next(i for i, b in enumerate(bars) if b[0].date() >= days[0])
    sh = lambda i: i - start
    r = lambda v: None if v is None else round(v, 2)
    doc = {
        "generated_ist": now_ist.strftime("%Y-%m-%d %H:%M"),
        "sym": SYM, "name": "NIFTY 50", "tf": "5m",
        "rules": {"swing": LEN, "max_age": MAX_AGE, "supertrend": [ST_ATR, ST_FACTOR], "trend_filter": TREND_FILTER},
        "bars": [[b[0].strftime("%Y-%m-%d %H:%M"), r(b[1]), r(b[2]), r(b[3]), r(b[4])] for b in bars[start:]],
        "st": [[r(st[i]), dr[i]] for i in range(start, len(bars))],
        "lines": [], "signals": [],
    }
    tri_pairs = []
    for ln in lines:
        if ln.x2 < start:
            continue
        x1 = max(ln.x1, start)
        doc["lines"].append({"k": ln.kind, "x1": sh(x1), "y1": r(ln.at(x1)), "x2": sh(ln.x2), "y2": r(ln.at(ln.x2)),
                             "broken": ln.broken, "tri": ln.tri})
        if ln.kind == "res" and getattr(ln, "tri_with", None) is not None:
            s = ln.tri_with
            a = max(ln.x1, s.x1, start)
            b = min(ln.x2, s.x2)
            if b > a:
                tri_pairs.append({"x1": sh(a), "x2": sh(b),
                                  "top": [r(ln.at(a)), r(ln.at(b))], "bot": [r(s.at(a)), r(s.at(b))]})
    doc["triangles"] = tri_pairs
    for sg in signals:
        if sg["i"] >= start:
            doc["signals"].append(dict(sg, i=sh(sg["i"]), t=bars[sg["i"]][0].strftime("%Y-%m-%d %H:%M")))
    last_dir = dr[-1]
    doc["trend"] = None if last_dir is None else ("up" if last_dir < 0 else "down")
    doc["last_bar"] = bars[-1][0].strftime("%Y-%m-%d %H:%M")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    today = [s for s in doc["signals"] if s["t"].startswith(days[-1].isoformat())]
    print("signals.json: %d candles over %d days, %d signals (%d on %s), trend %s"
          % (len(doc["bars"]), len(days), len(doc["signals"]), len(today), days[-1], doc["trend"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
