#!/usr/bin/env python3
"""
cap_clean.py - is the multiplier loss cap a real edge, or just leveraged drift?

Two contaminations removed:

 1. DRIFT. The raw test's "uncapped EV" scaled linearly with the multiplier -
    that is the sample's (statistically insignificant) drift amplified.
    Here every series is DE-MEANED, so its drift is exactly zero by
    construction. Anything left is the cap and only the cap.

 2. COMMISSION. Deriv charges commission on multiplier trades. Enter the
    real rate and it is deducted. Default 0 shows the gross effect.

  python3 cap_clean.py [--comm 0.003] [--hold 500]

Reports, per multiplier tier: EV with the cap on de-meaned data, plus a
bootstrap confidence interval - because with ~90 spikes per symbol the
sampling error is large and must be shown, not hidden.
"""
import math, os, random, statistics as st, sys

TICKS = os.path.expanduser("~/goldlab/data/ticks")
random.seed(2024)
SYMS = [("Boom_1000_Index", +1), ("Crash_1000_Index", -1),
        ("Boom_500_Index", +1), ("Crash_500_Index", -1)]
TIERS = (100, 200, 300, 400, 500, 1000)


def load(s):
    p = os.path.join(TICKS, s + "_ticks.csv")
    if not os.path.exists(p): return []
    r = []
    for ln in open(p):
        a = ln.strip().split(",")
        if len(a) == 2 and a[0].isdigit():
            try: r.append(float(a[1]))
            except Exception: pass
    return r


def demean(px):
    """rebuild the path with exactly zero mean log-drift"""
    lr = [math.log(px[i]/px[i-1]) for i in range(1, len(px))]
    m = sum(lr)/len(lr)
    out = [px[0]]
    for x in lr:
        out.append(out[-1]*math.exp(x - m))
    return out


def trade(px, M, direction, hold, comm, n=4000):
    """direction: -1 short. Returns list of P&L per $1 stake."""
    res = []
    N = len(px)
    cut = 1.0/M
    for _ in range(n):
        i = random.randrange(200, N-hold-2)
        e = px[i]
        pnl = None
        for j in range(i+1, i+1+hold):
            mv = direction*(px[j]-e)/e
            if mv <= -cut:
                pnl = -1.0          # capped: never worse than the stake
                break
        if pnl is None:
            pnl = M*(direction*(px[i+hold]-e)/e)
        res.append(pnl - comm)
    return res


def boot(vals, n=400):
    ms = []
    for _ in range(n):
        s = [vals[random.randrange(len(vals))] for _ in range(len(vals))]
        ms.append(sum(s)/len(s))
    ms.sort()
    return ms[int(0.025*n)], ms[int(0.975*n)]


def main():
    a = sys.argv[1:]
    comm = 0.0; hold = 500
    if "--comm" in a: comm = float(a[a.index("--comm")+1])
    if "--hold" in a: hold = int(a[a.index("--hold")+1])
    print("DE-MEANED (drift removed). hold " + str(hold)
          + " ticks, commission " + str(comm) + " per $1 stake.\n")
    for sym, sdir in SYMS:
        px = load(sym)
        if len(px) < 20000: continue
        dm = demean(px)
        d = [dm[i]-dm[i-1] for i in range(1, len(dm))]
        med = st.median([abs(x) for x in d])
        sp = [abs(x)/dm[0] for x in d if abs(x) > 10*med]
        msp = st.median(sp) if sp else 0
        print("="*68)
        print(sym + "   median spike " + ("%.4f%%" % (100*msp))
              + "   trading AGAINST the spike")
        print("   mult   stop-out    spike>cut     EV per $1        95% CI")
        for M in TIERS:
            cut = 1.0/M
            through = sum(1 for s in sp if s > cut)/len(sp) if sp else 0
            r = trade(dm, M, -sdir, hold, comm)
            ev = sum(r)/len(r)
            lo, hi = boot(r)
            sig = "  <-- CI above 0" if lo > 0 else ""
            print("   x" + str(M).ljust(6)
                  + ("%.3f%%" % (100*cut)).rjust(9)
                  + ("%.1f%%" % (100*through)).rjust(11)
                  + ("%+.4f" % ev).rjust(13)
                  + ("[%+.4f, %+.4f]" % (lo, hi)).rjust(22) + sig)
        print()
    print("A real edge needs the whole CI above zero AND a multiplier tier")
    print("Deriv actually offers on that symbol. Check the platform.")


main()
