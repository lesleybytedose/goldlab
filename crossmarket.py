#!/usr/bin/env python3
"""
crossmarket.py - run the frozen CRT+MSS spec on markets it was never shaped on.

No tuning. Same rules, same parameters, different data. If the structure is
real it shows up elsewhere. If only gold works, it was fitted to gold.

  python3 crossmarket.py
"""
import json, os, ssl, sys, urllib.request
from datetime import datetime, timezone, timedelta

RAW = os.path.expanduser("~/goldlab/data/raw")
SAST = timezone(timedelta(hours=2))

MARKETS = [
    ("GC=F",  "Gold      (shaped on this)"),
    ("SI=F",  "Silver"),
    ("EURUSD=X", "EUR/USD"),
    ("ES=F",  "S&P 500 futures"),
    ("CL=F",  "Crude oil"),
    ("BTC-USD", "Bitcoin"),
]


def fetch(sym):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(sym)}?interval=1h&range=2y")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=45,
                                context=ssl.create_default_context()) as r:
        d = json.loads(r.read().decode())
    res = d["chart"]["result"][0]
    ts, q = res["timestamp"], res["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        out.append((datetime.fromtimestamp(t, timezone.utc).astimezone(SAST),
                    o, h, l, c))
    return out


def main():
    import urllib.parse
    import crt as C

    print("\n" + "=" * 92)
    print("  FROZEN CRT + MSS SPEC — CROSS MARKET TEST")
    print("=" * 92)
    print("  Rules unchanged from the gold run. Spread scaled to each market's")
    print("  own volatility so costs stay comparable.\n")
    print(f"  {'MARKET':30} {'bars':>6} {'setups':>7} {'win':>7} "
          f"{'exp':>8} {'pf':>6}   halves")
    print("-" * 92)

    results = []
    for sym, label in MARKETS:
        try:
            bars = fetch(sym)
        except Exception as e:
            print(f"  {label:30} fetch failed: {e}")
            continue
        if len(bars) < 2000:
            print(f"  {label:30} only {len(bars)} bars, skipped")
            continue

        # scale cost to the instrument: 0.006% of price, gold-equivalent
        C.SPREAD = bars[-1][4] * 0.00006
        setups = C.find_setups(bars)
        rows = [(s, C.simulate(s, bars)) for s in setups]
        R = [r for _, r in rows if r is not None]

        if len(R) < 15:
            print(f"  {label:30} {len(bars):>6} {len(R):>7}   too few setups")
            continue

        w = [x for x in R if x > 0]
        l = [x for x in R if x <= 0]
        exp = sum(R) / len(R)
        pf = sum(w) / abs(sum(l)) if l else 99.0
        half = len(R) // 2
        e1 = sum(R[:half]) / half
        e2 = sum(R[half:]) / (len(R) - half)
        results.append((label, len(R), exp, pf, e1, e2))
        mark = "  <--" if exp > 0.2 and e1 > 0 and e2 > 0 else ""
        print(f"  {label:30} {len(bars):>6} {len(R):>7} {len(w)/len(R):>6.1%} "
              f"{exp:>+7.2f}R {pf:>6.2f}   {e1:+.2f}/{e2:+.2f}{mark}")

    print("-" * 92)
    others = [r for r in results if "shaped" not in r[0]]
    pos = [r for r in others if r[2] > 0.15]
    print()
    if not others:
        print("  Not enough data from other markets to judge.")
    elif len(pos) >= max(2, len(others) // 2):
        print(f"  HOLDS UP. Positive on {len(pos)} of {len(others)} other markets.")
        print("  The structure is doing something that is not specific to gold.")
        print("  Still a small sample per market — treat as encouraging, not settled.")
    elif len(pos) == 0:
        print("  FAILED. Gold only. Every other market came back flat or negative.")
        print("  That is what a rule fitted to one dataset looks like.")
        print("  The gold result was almost certainly the shape of the last two years.")
    else:
        print(f"  MIXED. Only {len(pos)} of {len(others)} other markets positive.")
        print("  Not enough to call it a real structural effect. Treat as unproven.")
    print("=" * 92 + "\n")


if __name__ == "__main__":
    main()
