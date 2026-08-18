#!/usr/bin/env python3
"""
accu_ticks.py - collect Deriv tick history, then measure whether Boom/Crash
spike spacing is memoryless.

Two commands:

  python3 accu_ticks.py fetch [SYMBOL ...]     pull tick history -> data/ticks/
  python3 accu_ticks.py spikes [SYMBOL ...]    spacing analysis on what we have

Read-only with respect to the rest of GoldLab. Writes only under data/ticks/.
No account, no token, no trading: ticks_history is a public endpoint.

The question it answers: after a Boom/Crash spike, is the next spike any
more predictable than a coin flip? If gaps are geometric (memoryless),
"open an accumulator right after a spike" has no edge and the idea dies
here. If gaps are markedly regular, tick-level testing is justified.
"""
import json, math, os, ssl, statistics as st, sys, time
from datetime import datetime, timezone

HOME = os.path.expanduser("~/goldlab")
OUT = os.path.join(HOME, "data/ticks")
WS = "wss://ws.derivws.com/websockets/v3?app_id=1089"   # public demo app_id

SYMS = {
    "Boom_1000_Index": "BOOM1000",
    "Crash_1000_Index": "CRASH1000",
    "Volatility_75_Index": "R_75",
}
CHUNK = 5000          # ticks per request (Deriv max)
TARGET = 100000       # default ticks to collect per symbol


def fetch(symbol, target=TARGET):
    """Walk backwards through history in 5000-tick chunks."""
    try:
        from websocket import create_connection      # pip install websocket-client
    except ImportError:
        print("  need: pip3 install --user websocket-client")
        sys.exit(1)

    code = SYMS.get(symbol, symbol)
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{symbol}_ticks.csv")

    have = {}
    if os.path.exists(path):
        for ln in open(path):
            p = ln.strip().split(",")
            if len(p) == 2 and p[0].isdigit():
                have[int(p[0])] = p[1]
        print(f"  {symbol}: {len(have):,} ticks already stored")

    ws = create_connection(WS, timeout=30,
                           sslopt={"cert_reqs": ssl.CERT_REQUIRED})
    end = "latest"
    got = 0
    try:
        while got < target:
            ws.send(json.dumps({
                "ticks_history": code, "end": end, "count": CHUNK,
                "style": "ticks", "adjust_start_time": 1}))
            r = json.loads(ws.recv())
            if "error" in r:
                print(f"  {symbol}: API said: {r['error'].get('message')}")
                break
            h = r.get("history") or {}
            times, prices = h.get("times") or [], h.get("prices") or []
            if not times:
                print(f"  {symbol}: no more history")
                break
            new = 0
            for t, px in zip(times, prices):
                if t not in have:
                    have[t] = str(px); new += 1
            got += new
            print(f"  {symbol}: +{new:,} new (total {len(have):,})", flush=True)
            if new == 0:
                break
            end = str(min(times) - 1)     # step further back
            time.sleep(0.4)               # be polite to the endpoint
    finally:
        ws.close()

    with open(path, "w") as f:
        f.write("epoch,price\n")
        for t in sorted(have):
            f.write(f"{t},{have[t]}\n")
    print(f"  {symbol}: saved {len(have):,} ticks -> {path}")


def load(symbol):
    path = os.path.join(OUT, f"{symbol}_ticks.csv")
    if not os.path.exists(path):
        return []
    out = []
    for ln in open(path):
        p = ln.strip().split(",")
        if len(p) == 2 and p[0].isdigit():
            try: out.append((int(p[0]), float(p[1])))
            except Exception: pass
    out.sort()
    return out


def spikes(symbol):
    ticks = load(symbol)
    if len(ticks) < 5000:
        print(f"\n{symbol}: only {len(ticks):,} ticks — run fetch first")
        return
    px = [p for _, p in ticks]
    diffs = [px[i] - px[i-1] for i in range(1, len(px))]
    absd = [abs(d) for d in diffs]
    med = st.median(absd)
    print(f"\n{symbol}: {len(ticks):,} ticks, median |tick move| {med:.4f}")

    # a spike is a single tick move far outside the normal distribution
    for mult in (10, 20, 50):
        up = [i for i, d in enumerate(diffs) if d > mult * med]
        dn = [i for i, d in enumerate(diffs) if d < -mult * med]
        which, idx = ("up", up) if len(up) >= len(dn) else ("down", dn)
        if len(idx) < 30:
            print(f"  {mult}x median: only {len(idx)} {which}-spikes — skip")
            continue
        gaps = [idx[i] - idx[i-1] for i in range(1, len(idx))]
        m = sum(gaps) / len(gaps)
        sd = st.pstdev(gaps)
        cv = sd / m if m else 0
        print(f"  {mult}x median: {len(idx)} {which}-spikes, "
              f"1 per {len(diffs)/len(idx):.0f} ticks")
        print(f"      gap mean {m:.0f}, sd {sd:.0f}, min {min(gaps)}, "
              f"max {max(gaps)}, CV {cv:.3f}")
        # geometric benchmark: CV -> ~1.0 for memoryless
        print(f"      memoryless would give CV ~= {math.sqrt(1-1/m):.3f}"
              if m > 1 else "")
        # hazard in the 200 ticks right after a spike vs everywhere else
        after = set()
        for i in idx:
            after.update(range(i+1, i+201))
        n_after = len(after)
        sp_after = sum(1 for i in idx if i in after)
        rate_after = sp_after / n_after if n_after else 0
        rate_all = len(idx) / len(diffs)
        print(f"      spike rate in the 200 ticks after a spike: "
              f"{rate_after*1000:.2f} per 1000  vs  overall {rate_all*1000:.2f} per 1000")
        if rate_all:
            print(f"      ratio {rate_after/rate_all:.2f}  "
                  f"(1.00 = memoryless, <1 = refractory period, >1 = clustering)")


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    syms = sys.argv[2:] or list(SYMS)
    if cmd == "fetch":
        for s in syms:
            fetch(s)
    elif cmd == "spikes":
        for s in syms:
            spikes(s)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
