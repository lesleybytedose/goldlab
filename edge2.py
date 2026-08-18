#!/usr/bin/env python3
"""
edge2.py - follow up the two things worth following up.

 A) PERMUTATION TEST of the Crash_500 lag-5 autocorrelation. The naive
    t-stat assumes normal data; these returns are wildly fat-tailed, so
    we shuffle instead and see where the real value sits.
 B) COST HURDLE across EVERY feed we run, gold included, ranked. This is
    the per-trade tax a model must beat, and it is measured, not guessed.
"""
import math, os, random, statistics as st, sys

TICKS = os.path.expanduser("~/goldlab/data/ticks")
RAW = os.path.expanduser("~/goldlab/data/raw")
random.seed(101)


def load_ticks(s):
    p = os.path.join(TICKS, s + "_ticks.csv")
    if not os.path.exists(p): return []
    r = []
    for ln in open(p):
        a = ln.strip().split(",")
        if len(a) == 2 and a[0].isdigit():
            try: r.append(float(a[1]))
            except Exception: pass
    return r


def acf(x, lag):
    n = len(x)-lag
    m = sum(x)/len(x)
    num = sum((x[i]-m)*(x[i+lag]-m) for i in range(n))
    den = sum((v-m)**2 for v in x)
    return num/den if den else 0.0


print("A) PERMUTATION TEST - is any lag really autocorrelated?")
print("   shuffling destroys order but keeps the fat tails.")
for sym, lag in (("Crash_500_Index", 5), ("Boom_500_Index", 3),
                 ("Boom_1000_Index", 2), ("Volatility_100_Index", 3)):
    px = load_ticks(sym)
    if len(px) < 20000:
        print("   " + sym + ": no ticks"); continue
    d = [px[i]-px[i-1] for i in range(1, len(px))]
    obs = acf(d, lag)
    worse = 0
    N = 400
    for _ in range(N):
        sh = d[:]; random.shuffle(sh)
        if abs(acf(sh, lag)) >= abs(obs): worse += 1
    pv = (worse+1)/(N+1)
    naive_t = obs*math.sqrt(len(d))
    verdict = "REAL" if pv < 0.01 else "noise (fat tails fooled the t-stat)"
    print("   " + sym.ljust(21) + " lag " + str(lag)
          + "   r=" + ("%+.5f" % obs)
          + "   naive t=" + ("%+.2f" % naive_t)
          + "   permutation p=" + str(round(pv, 4)) + "   " + verdict)

print("\nB) COST HURDLE - what every trade pays before any edge exists")
rows = []
for f in sorted(os.listdir(RAW)):
    if not f.endswith("_M15_live.csv"): continue
    sym = f.replace("_M15_live.csv", "")
    sp, rng = [], []
    lines = open(os.path.join(RAW, f)).read().splitlines()[1:]
    for ln in lines[-3000:]:
        a = ln.split(",")
        if len(a) < 7: continue
        try:
            h, l, s = float(a[3]), float(a[4]), float(a[6])
            if s > 0 and h > l: sp.append(s); rng.append(h-l)
        except Exception: pass
    if len(sp) < 100: continue
    ms, mr = st.median(sp), st.median(rng)
    rows.append((ms/mr, sym, ms, mr, len(sp)))
rows.sort()
print("   " + "symbol".ljust(22) + "spread".rjust(10) + "bar range".rjust(12)
      + "cost/R".rjust(10) + "   per 1000 trades")
for ratio, sym, ms, mr, n in rows:
    print("   " + sym.ljust(22) + str(round(ms, 4)).rjust(10)
          + str(round(mr, 2)).rjust(12)
          + (str(round(100*ratio, 2)) + "%").rjust(10)
          + ("   -" + str(round(1000*ratio, 1)) + "R").rjust(18))
if rows:
    best, worst = rows[0], rows[-1]
    print("\n   cheapest: " + best[1] + " at " + str(round(100*best[0], 2)) + "% of R")
    print("   dearest:  " + worst[1] + " at " + str(round(100*worst[0], 2)) + "% of R")
    print("   ratio: " + str(round(worst[0]/best[0], 1))
          + "x more expensive to trade the same nothing")
