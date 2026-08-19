#!/usr/bin/env python3
"""
spike_levels.py - do Boom/Crash spikes happen AT price levels?

THE HYPOTHESIS (Lesley, 2026-08-19, before any result was seen)
  Spikes occur preferentially where price meets a marked level - a
  descending trendline, a prior high, a multi-rule cluster - rather than
  uniformly through the range.

  This is DIFFERENT from the tick-count hypothesis already tested and
  rejected. That one asked whether elapsed TIME predicts a spike, and the
  answer was no (flat hazard, cv ~0.9, random entries beat timed ones).
  This asks whether LOCATION does.

WHAT IS MEASURED
  1. spike rate when price is AT a multi-rule level vs when it is not
  2. spike SIZE at levels vs away from them
  3. the same two, but against RANDOMLY DISPLACED levels, so that "price
     often sits near some level" cannot masquerade as a finding

  A real effect must beat the displaced-level control, not just differ
  from the base rate.

NOTE ON WHAT WOULD MAKE IT PLAUSIBLE
  Deriv generates these series; a jump process that also consults its own
  chart would be an odd design. So the prior is low. That is a reason to
  test carefully, not a reason to skip the test.

  python3 spike_levels.py
"""
import csv, glob, hashlib, math, os, statistics, sys

HOME = os.path.expanduser("~/goldlab")
RAW = os.path.join(HOME, "data/raw")
TOL_ATR = 0.25          # "at a level" means within this many ATR
MIN_RULES = 2           # a level must be marked by at least this many rules


def load(path):
    b = []
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                b.append((float(r["open"]), float(r["high"]), float(r["low"]),
                          float(r["close"]), float(r.get("spread") or 0)))
            except Exception:
                pass
    return b


def atr_series(b, n=60):
    out, trs = [], []
    for i, x in enumerate(b):
        tr = x[1]-x[2] if i == 0 else max(x[1]-x[2], abs(x[1]-b[i-1][3]),
                                          abs(x[2]-b[i-1][3]))
        trs.append(tr)
        out.append(sum(trs[-n:]) / min(len(trs), n))
    return out


def levels_at(b, i, look=400):
    """Rule-based levels visible at bar i. No lookahead: only bars <= i.
    Swing detection leaves the last 2 bars unconfirmed, as a swing needs
    bars on both sides to exist."""
    L = []
    lo = max(0, i - look)
    seg = b[lo:i+1]
    if len(seg) < 40:
        return L
    # prior highs / lows over several windows (the 'horizontal' family)
    for w in (60, 120, 240):
        if i - w >= 0:
            L.append((max(x[1] for x in b[i-w:i-1]), f"high{w}"))
            L.append((min(x[2] for x in b[i-w:i-1]), f"low{w}"))
    # confirmed swing highs / lows (the 'structure' family)
    k = 2
    for j in range(lo + k, i - k):
        hs = [b[t][1] for t in range(j-k, j+k+1)]
        ls = [b[t][2] for t in range(j-k, j+k+1)]
        if b[j][1] == max(hs):
            L.append((b[j][1], "swing_high"))
        if b[j][2] == min(ls):
            L.append((b[j][2], "swing_low"))
    # round numbers (the 'magnet' family)
    p = b[i][3]
    step = 500.0 if p > 10000 else 50.0 if p > 1000 else 5.0
    L.append(((p // step) * step, "round"))
    L.append(((p // step) * step + step, "round"))
    return L


def at_level(b, i, a, shift=0.0):
    """How many distinct rules mark a level within TOL_ATR of this bar's
    close. `shift` displaces every level by a fraction of ATR - the
    control condition."""
    if a[i] <= 0:
        return 0
    p = b[i][3]
    tol = TOL_ATR * a[i]
    off = shift * a[i]
    hits = set()
    for lv, src in levels_at(b, i):
        if abs((lv + off) - p) <= tol:
            hits.add(src.rstrip("0123456789"))
    return len(hits)


def analyse(path, spike_mult=5.0):
    sym = os.path.basename(path).replace("_live.csv", "")
    b = load(path)
    if len(b) < 2000:
        print(f"  {sym}: {len(b)} bars, need 2000+"); return
    a = atr_series(b)
    spike = [(b[i][1]-b[i][2]) > spike_mult*a[i] if a[i] > 0 else False
             for i in range(len(b))]
    n_sp = sum(spike)
    print(f"\n  {sym}   {len(b):,} bars   {n_sp} spikes")
    print("  " + "=" * 68)

    def run(shift, label):
        at_n = at_off = 0
        sp_at = sp_off = 0
        size_at, size_off = [], []
        for i in range(400, len(b)):
            if a[i] <= 0:
                continue
            lv = at_level(b, i, a, shift)
            isat = lv >= MIN_RULES
            if isat:
                at_n += 1
            else:
                at_off += 1
            if spike[i]:
                if isat:
                    sp_at += 1; size_at.append((b[i][1]-b[i][2]) / a[i])
                else:
                    sp_off += 1; size_off.append((b[i][1]-b[i][2]) / a[i])
        r_at = sp_at / at_n if at_n else 0
        r_off = sp_off / at_off if at_off else 0
        # two-proportion z
        p = (sp_at + sp_off) / max(1, at_n + at_off)
        se = math.sqrt(max(p*(1-p)*(1/max(1, at_n) + 1/max(1, at_off)), 1e-12))
        z = (r_at - r_off) / se if se else 0
        print(f"  {label}")
        print(f"     bars at a {MIN_RULES}+ rule level: {at_n:,}   "
              f"away: {at_off:,}")
        print(f"     spike rate AT level {r_at:.3%}   away {r_off:.3%}   "
              f"z = {z:+.2f}")
        if size_at and size_off:
            print(f"     spike size AT {statistics.median(size_at):.1f} ATR   "
                  f"away {statistics.median(size_off):.1f} ATR   "
                  f"(n={len(size_at)} / {len(size_off)})")
        return z

    z_real = run(0.0, "REAL LEVELS")
    zs = []
    for k, sh in enumerate((0.8, -0.8, 1.6, -1.6, 2.4)):
        zs.append(run(sh, f"CONTROL: levels displaced by {sh:+.1f} ATR"))
    print("  " + "-" * 68)
    worst = max(abs(x) for x in zs)
    print(f"  real |z| = {abs(z_real):.2f}   biggest control |z| = {worst:.2f}")
    if abs(z_real) > 2 and abs(z_real) > worst:
        print("  -> spikes DO cluster at real levels more than at displaced ones.")
        print("     Pre-register an entry rule and forward test it before")
        print("     believing this.")
    else:
        print("  -> no location effect: spikes are no more likely at real")
        print("     levels than at deliberately wrong ones.")


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    paths = [p for p in sorted(glob.glob(os.path.join(RAW, "*_live.csv")))
             if ("Boom" in p or "Crash" in p)]
    if only:
        paths = [p for p in paths if only.lower() in os.path.basename(p).lower()]
    if not paths:
        print("  no Boom/Crash feeds found"); return
    for p in paths:
        analyse(p)
    print()


if __name__ == "__main__":
    main()
