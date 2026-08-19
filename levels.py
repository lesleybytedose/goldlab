#!/usr/bin/env python3
"""
levels.py - mechanical levels, structure, and level CLUSTERS for every
live feed. Computed from stored M15 bars, aggregated to H1 / H4 / D1.

Reports and feeds the system. Never predicts.

  python3 levels.py                  every live feed, human readable
  python3 levels.py XAUUSDm          one symbol
  python3 levels.py V75 H4           one symbol, one timeframe
  python3 levels.py --json           write logs/levels.json for other scripts
  python3 levels.py --stale          include feeds that stopped updating

WHAT IT ADDS BEYOND A CHART:
  * every level is produced by a NAMED RULE, so it can be argued with
  * levels within 0.25 ATR of each other are merged into a CLUSTER, and
    the cluster carries the list of independent rules that landed there.
    A price where the H4 swing low, S1 pivot and yesterday's low coincide
    is a 3-source cluster; a lone round number is a 1-source cluster.
  * each cluster names the deployed models that measure against that kind
    of level, so you can look up their forward record before acting
  * --json writes the same data for alerts/forming to consume

The cluster count is a HYPOTHESIS, not a finding: "levels where several
independent rules agree matter more" is exactly the sort of claim this
lab exists to test. It is reported, never acted on automatically.
"""
import csv, glob, json, os, sys
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~/goldlab")
RAW = os.path.join(HOME, "data/raw")
LOGS = os.path.join(HOME, "logs")
UTC = timezone.utc
SAST = timezone(timedelta(hours=2))
TFS = {"H1": 4, "H4": 16, "D1": 96}
STALE_MIN = 120          # feeds quieter than this are skipped unless --stale
MERGE_ATR = 0.25         # levels closer than this fraction of ATR merge

# which deployed models measure against which kind of level
USED_BY = {
    "prior day high":   ["Failure test", "M1 daily extreme"],
    "prior day low":    ["Failure test", "M1 daily extreme"],
    "prior day close":  ["Break-retest pivot"],
    "pivot R1":         ["Break-retest pivot"],
    "pivot S1":         ["Break-retest pivot"],
    "pivot P":          ["Break-retest pivot"],
    "opening range high": ["M1 daily extreme", "London break"],
    "opening range low":  ["M1 daily extreme", "London break"],
    "today high":       ["M1 daily extreme", "CRT sweep"],
    "today low":        ["M1 daily extreme", "CRT sweep"],
    "donchian 20 high": ["Donchian SAR"],
    "donchian 20 low":  ["Donchian SAR"],
}


def load(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                d = datetime.fromisoformat(f"{r['date']} {r['time']}").replace(tzinfo=UTC)
                rows.append([d, float(r["open"]), float(r["high"]), float(r["low"]),
                             float(r["close"]), float(r.get("spread") or 0),
                             float(r.get("volume") or 0)])
            except Exception:
                pass
    rows.sort(key=lambda b: b[0])
    return rows


def agg(bs):
    return [bs[0][0], bs[0][1], max(x[2] for x in bs), min(x[3] for x in bs),
            bs[-1][4], bs[-1][5], sum(x[6] for x in bs)]


def resample(b, n):
    out, bucket = [], []
    for x in b:
        mins = x[0].hour * 60 + x[0].minute
        idx = mins // (15 * n)
        if bucket and bucket[0][0].date() == x[0].date() and \
           (bucket[0][0].hour * 60 + bucket[0][0].minute) // (15 * n) == idx:
            bucket.append(x)
        else:
            if bucket:
                out.append(agg(bucket))
            bucket = [x]
    if bucket:
        out.append(agg(bucket))
    return out


def atr(b, n=14):
    if len(b) < 2:
        return 0.0
    trs = [max(b[i][2]-b[i][3], abs(b[i][2]-b[i-1][4]), abs(b[i][3]-b[i-1][4]))
           for i in range(1, len(b))]
    return sum(trs[-n:]) / min(len(trs), n)


def swings(b, k=2):
    out = []
    for i in range(k, len(b) - k):
        hs = [b[j][2] for j in range(i-k, i+k+1)]
        ls = [b[j][3] for j in range(i-k, i+k+1)]
        if b[i][2] == max(hs): out.append((b[i][2], "high", i))
        if b[i][3] == min(ls): out.append((b[i][3], "low", i))
    return out


def structure(b, sw):
    hi = [s for s in sw if s[1] == "high"][-2:]
    lo = [s for s in sw if s[1] == "low"][-2:]
    seq, ma = None, None
    if len(hi) == 2 and len(lo) == 2:
        hh, hl = hi[1][0] > hi[0][0], lo[1][0] > lo[0][0]
        seq = ("higher highs, higher lows" if hh and hl else
               "lower highs, lower lows" if not hh and not hl else
               "mixed swings - range or transition")
    if len(b) >= 200:
        s50 = sum(x[4] for x in b[-50:]) / 50
        s200 = sum(x[4] for x in b[-200:]) / 200
        c = b[-1][4]
        ma = (f"price {'above' if c > s200 else 'below'} 200 ({s200:,.2f}), "
              f"50 {'above' if s50 > s200 else 'below'} 200")
    return seq, ma


def step_for(px):
    if px > 10000: return 500.0
    if px > 1000: return 50.0
    if px > 100: return 5.0
    if px > 10: return 1.0
    return 0.1


def collect(b15, px):
    """Every level from every rule, tagged with its source."""
    L = []   # (price, source, timeframe)
    days = {}
    for x in b15:
        days.setdefault(x[0].date(), []).append(x)
    dl = sorted(days)

    if len(dl) >= 2:
        y = days[dl[-2]]
        yh, yl, yc = max(x[2] for x in y), min(x[3] for x in y), y[-1][4]
        L += [(yh, "prior day high", "D1"), (yl, "prior day low", "D1"),
              (yc, "prior day close", "D1")]
        p = (yh + yl + yc) / 3.0; rng = yh - yl
        L += [(p + rng, "pivot R2", "D1"), (2*p - yl, "pivot R1", "D1"),
              (p, "pivot P", "D1"), (2*p - yh, "pivot S1", "D1"),
              (p - rng, "pivot S2", "D1")]
    if dl:
        t = days[dl[-1]]
        L += [(max(x[2] for x in t), "today high", "D1"),
              (min(x[3] for x in t), "today low", "D1")]
        if len(t) >= 8:
            L += [(max(x[2] for x in t[:8]), "opening range high", "D1"),
                  (min(x[3] for x in t[:8]), "opening range low", "D1")]
    # donchian 20 on M15 - what Donchian SAR actually watches
    if len(b15) > 21:
        L += [(max(x[2] for x in b15[-21:-1]), "donchian 20 high", "M15"),
              (min(x[3] for x in b15[-21:-1]), "donchian 20 low", "M15")]
    # swings per timeframe
    for tf, n in TFS.items():
        bt = resample(b15, n)
        if len(bt) < 30:
            continue
        for pxl, kind, _ in swings(bt)[-40:]:
            L.append((pxl, f"{tf} swing {kind}", tf))
    st = step_for(px)
    L += [((px // st) * st, "round number", "-"),
          ((px // st) * st + st, "round number", "-")]
    return L


def cluster(L, px, A):
    """Merge levels within MERGE_ATR of each other. Returns sorted clusters
    with their contributing rules."""
    if A <= 0:
        A = max(px * 0.001, 1e-9)
    tol = MERGE_ATR * A
    out = []
    for price, src, tf in sorted(L):
        if out and abs(price - out[-1]["price"]) <= tol:
            c = out[-1]
            c["sources"].append(src)
            c["price"] = sum(c["prices"] + [price]) / (len(c["prices"]) + 1)
            c["prices"].append(price)
        else:
            out.append(dict(price=price, prices=[price], sources=[src]))
    for c in out:
        c["n"] = len(set(c["sources"]))
        c["sources"] = sorted(set(c["sources"]))
        c["dist"] = c["price"] - px
        c["atr"] = c["dist"] / A if A else 0
        c["models"] = sorted({m for s in c["sources"] for m in USED_BY.get(s, [])})
        c.pop("prices", None)
    return out


def report(sym, path, want_tf=None, quiet=False):
    b15 = load(path)
    if len(b15) < 300:
        return None
    px = b15[-1][4]
    last_t = b15[-1][0]
    age = (datetime.now(UTC) - last_t).total_seconds() / 60
    A15 = atr(b15)
    L = collect(b15, px)
    cl = cluster(L, px, A15)
    above = [c for c in cl if c["dist"] > 0][:6]
    below = [c for c in cl if c["dist"] <= 0][-6:][::-1]

    data = dict(symbol=sym, price=round(px, 3), atr_m15=round(A15, 3),
                last=last_t.isoformat(), age_min=round(age),
                above=above, below=below, structure={})
    for tf, n in TFS.items():
        bt = resample(b15, n)
        if len(bt) < 30:
            continue
        seq, ma = structure(bt, swings(bt))
        data["structure"][tf] = dict(seq=seq, ma=ma, atr=round(atr(bt), 3))

    if quiet:
        return data

    print(f"\n  {sym}   {px:,.2f}   {last_t.astimezone(SAST):%a %H:%M} SAST "
          f"({age:.0f}m ago)   M15 ATR {A15:,.2f}")
    print("  " + "=" * 70)
    for tf in ("H1", "H4", "D1"):
        s = data["structure"].get(tf)
        if not s:
            continue
        if want_tf and tf != want_tf:
            continue
        print(f"  {tf:>3}  {s['seq'] or '-'}")
        if s["ma"]:
            print(f"       {s['ma']}")
    print(f"\n  {'RESISTANCE above':<42}{'dist':>10}{'ATR':>7}{'src':>5}")
    for c in above:
        print(f"    {c['price']:>12,.2f}  {', '.join(c['sources'])[:26]:<26}"
              f"{c['dist']:>+10,.2f}{c['atr']:>+7.1f}{c['n']:>5}")
        if c["models"]:
            print(f"                 tested by: {', '.join(c['models'])}")
    print(f"\n  {'SUPPORT below':<42}{'dist':>10}{'ATR':>7}{'src':>5}")
    for c in below:
        print(f"    {c['price']:>12,.2f}  {', '.join(c['sources'])[:26]:<26}"
              f"{c['dist']:>+10,.2f}{c['atr']:>+7.1f}{c['n']:>5}")
        if c["models"]:
            print(f"                 tested by: {', '.join(c['models'])}")
    best = max(cl, key=lambda c: c["n"]) if cl else None
    if best and best["n"] >= 3:
        print(f"\n  densest cluster: {best['price']:,.2f} — {best['n']} independent "
              f"rules agree\n    ({', '.join(best['sources'])})")
    return data


def main():
    args = sys.argv[1:]
    as_json = "--json" in args
    keep_stale = "--stale" in args
    args = [a for a in args if not a.startswith("--")]
    want_tf = next((a.upper() for a in args if a.upper() in TFS), None)
    name = next((a for a in args if a.upper() not in TFS), None)

    paths = sorted(glob.glob(os.path.join(RAW, "*_M15_live.csv")))
    if name:
        paths = [p for p in paths if name.lower() in os.path.basename(p).lower()]
    out = {}
    for p in paths:
        sym = os.path.basename(p).replace("_M15_live.csv", "")
        try:
            d = report(sym, p, want_tf, quiet=as_json)
        except Exception as e:
            print(f"  {sym}: error {e}"); continue
        if not d:
            continue
        d["stale"] = d["age_min"] > STALE_MIN
        if d["stale"] and not keep_stale and not name and not as_json:
            print(f"  ({sym} skipped — last bar {d['age_min']}m ago; --stale to include)")
            continue
        out[sym] = d
    if as_json:
        os.makedirs(LOGS, exist_ok=True)
        p = os.path.join(LOGS, "levels.json")
        json.dump(dict(generated=datetime.now(UTC).isoformat(timespec="seconds"),
                       symbols=out), open(p, "w"))
        print(f"  wrote {p}  ({len(out)} symbols)")
    elif not as_json:
        print("\n  Measured levels, not predictions. Cluster count is a hypothesis")
        print("  this lab has not yet tested — check each model's forward record")
        print("  before treating any level as tradeable.\n")


if __name__ == "__main__":
    main()
