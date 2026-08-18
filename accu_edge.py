#!/usr/bin/env python3
"""
accu_edge.py - where could an edge actually be? Everything from our ticks.

 1. SHAPE of the tick-move distribution (is it Gaussian? fat tails?
    what share of break-risk comes from spikes?)
 2. BREAK-EVEN BARRIER per growth rate - the number to compare against
    Deriv's actual barrier.
 3. EV LOOKUP TABLE across barrier widths - read Deriv's barrier off the
    platform, find the row, get your expectancy.
 4. GAUSSIAN MISPRICING CHECK - if Deriv prices off standard deviation
    but the real distribution is thinner/fatter, that gap is the edge.
"""
import math, os, statistics as st, sys

OUT = os.path.expanduser("~/goldlab/data/ticks")
DEF = ["Boom_1000_Index", "Crash_1000_Index", "Volatility_75_Index",
       "Volatility_25_Index", "Volatility_10_Index"]
GROWTHS = (0.01, 0.02, 0.03, 0.04, 0.05)


def load(s):
    p = os.path.join(OUT, s + "_ticks.csv")
    if not os.path.exists(p): return []
    r = []
    for ln in open(p):
        a = ln.strip().split(",")
        if len(a) == 2 and a[0].isdigit():
            try: r.append(float(a[1]))
            except Exception: pass
    return r


def q(xs, p):
    s = sorted(xs); return s[min(len(s)-1, int(p*(len(s)-1)))]


def best_ev(ad, bar, g, cap=230):
    """exact EV: survival is per-tick iid at this barrier."""
    p = sum(1 for x in ad if x <= bar)/len(ad)
    best, bestn = 0.0, 0
    for n in range(1, cap+1):
        ev = (p**n) * ((1+g)**n)
        if ev > best: best, bestn = ev, n
    return p, best, bestn


def run(sym):
    px = load(sym)
    if len(px) < 20000: return
    d = [px[i]-px[i-1] for i in range(1, len(px))]
    ad = [abs(x) for x in d]
    n = len(ad)
    med, sd = st.median(ad), st.pstdev(d)
    mean_abs = sum(ad)/n
    big = [x for x in ad if x > 10*med]
    print("\n" + "="*66)
    print(sym + "   " + str(n) + " tick moves")
    print("  1. SHAPE")
    print("     median |move| " + str(round(med, 6))
          + "   mean |move| " + str(round(mean_abs, 6))
          + "   sd " + str(round(sd, 6)))
    print("     sd / median = " + str(round(sd/med, 2))
          + "   (Gaussian would be ~1.48)")
    print("     spikes >10x median: " + str(len(big)) + " = "
          + str(round(100*len(big)/n, 3)) + "% of ticks, but "
          + str(round(100*sum(big)/sum(ad), 1)) + "% of all movement")
    # gaussian check: what fraction inside 1 sd
    inside = sum(1 for x in ad if x <= sd)/n
    print("     ticks within 1 sd: " + str(round(100*inside, 1))
          + "%   (Gaussian = 68.3%)  => "
          + ("FAT tails" if inside > 0.75 else "near-Gaussian" if inside > 0.62 else "THIN tails"))

    print("  2. BREAK-EVEN BARRIER (fair = EV 1.00)")
    for g in GROWTHS:
        w = q(ad, 1.0/(1.0+g))
        print("     " + str(int(g*100)) + "%: " + str(round(w, 6))
              + "   = " + str(round(w/med, 2)) + "x median move"
              + "   = " + str(round(w/sd, 3)) + "x sd")

    print("  3. EV LOOKUP - read Deriv's barrier, find your row (3% growth)")
    g = 0.03
    fair = q(ad, 1.0/(1.0+g))
    print("     barrier      P(tick inside)   best hold   EV per $1")
    for mult in (0.5, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 2.0, 3.0):
        bar = fair*mult
        p, ev, bn = best_ev(ad, bar, g)
        tag = "   <-- FAIR" if abs(mult-1.0) < 1e-9 else ""
        star = "  ** POSITIVE **" if ev > 1.05 else ""
        print("     " + str(round(bar, 6)).ljust(12)
              + str(round(100*p, 3)).rjust(8) + "%"
              + str(bn).rjust(11) + " ticks"
              + str(round(ev, 4)).rjust(11) + tag + star)

    print("  4. IF DERIV PRICES OFF STANDARD DEVIATION")
    for k in (0.5, 1.0, 1.5, 2.0):
        bar = k*sd
        p, ev, bn = best_ev(ad, bar, 0.03)
        print("     barrier = " + str(k) + " x sd = " + str(round(bar, 6))
              + "   EV " + str(round(ev, 4))
              + ("  ** a k x sd rule would be exploitable **" if ev > 1.05 else ""))


for s in (sys.argv[1:] or DEF):
    run(s)
