#!/usr/bin/env python3
"""live.json: the latest Yahoo price of the Nifty 50 index and its 50 shares, small
enough to rebuild every minute. The site reads it from the `live` branch and lays it
over data.json, so prices move every minute without a Pages deploy.

    python3 scripts/build_live.py [OUT]     # default live.json
"""

import datetime
import importlib.util
import json
import os
import sys
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("scan", os.path.join(ROOT, "scanner", "scan.py"))
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)


def symbols():
    out = ["^NSEI"]
    with open(os.path.join(ROOT, "scanner", "nifty50.txt"), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line.split("|")[0].strip())
    return out


def main(out="live.json"):
    syms = symbols()
    prices, failed = {}, []
    for i in range(0, len(syms), scan.SPARK_MAX):
        batch = syms[i:i + scan.SPARK_MAX]
        path = "/v7/finance/spark?symbols=%s&range=1d&interval=1m" % urllib.parse.quote(",".join(batch), safe=",")
        try:
            doc = json.loads(scan.yahoo_get(path))
        except Exception as e:  # one bad batch must not lose the others
            print("batch failed: %s" % e, file=sys.stderr)
            failed += batch
            continue
        for res in (doc.get("spark") or {}).get("result") or []:
            meta = ((res.get("response") or [{}])[0]).get("meta") or {}
            t, p = meta.get("regularMarketTime"), meta.get("regularMarketPrice")
            if t and p is not None:
                prices[res["symbol"]] = {"price": round(float(p), 2),
                                         "time": scan.ts_to_eastern(t).strftime("%Y-%m-%d %H:%M")}
        time.sleep(scan.PAUSE)
    if not prices:
        print("no prices", file=sys.stderr)
        return 1
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    doc = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "generated_ist": scan.to_eastern(now).strftime("%Y-%m-%d %H:%M"),
           "prices": prices, "failed": sorted(set(syms) - set(prices))}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    print("live.json: %d prices at %s IST" % (len(prices), doc["generated_ist"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
