#!/usr/bin/env python3
"""
refract.py - is there a refractory period after a spike? Decisive version.

The earlier tests disagreed:
  - spacing CV ~0.9 and post-spike ratio 1.23 said NO
  - min gap 33 on Crash and clean drift for 25 ticks after said MAYBE

Both were indirect. This asks the question directly and only:

  P(a spike occurs within the next H ticks | a spike just happened)
      vs
  P(a spike occurs within the next H ticks | random non-spike tick)

with an exact binomial comparison, and a geometric benchmark. If a
refractory period exists, the post-spike probability must be LOWER at
short H. If it is not, this closes the question for good.
"""
import math, os, random, statistics as st

TICKS = os.path.expanduser("~/goldlab/data/ticks")
random.seed(999)


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


def run(sym, up):
    px = load(sym)
    if len(px) < 20000: return
    d = [px[i]-px[i-1] for i in range(1, len(px))]
    med = st.median([abs(x) for x in d])
    sp = sorted(i for i, x in enumerate(d) if (x > 10*med if up else x < -10*med))
    spset = set(sp)
    n = len(d)
    rate = len(sp)/n
    print("\n" + "="*70)
    print(sym + "   " + str(len(sp)) + " spikes / " + str(n) + " ticks"
          + "   base rate " + str(round(1000*rate, 3)) + " per 1000")
    gaps = [sp[i]-sp[i-1] for i in range(1, len(sp))]
    gaps.sort()
    print("   gaps: min " + str(gaps[0]) + "  5th pct " + str(gaps[len(gaps)//20])
          + "  median " + str(gaps[len(gaps)//2]) + "  mean "
          + str(round(sum(gaps)/len(gaps))))
    # how surprising is the observed minimum under memoryless?
    mean_gap = sum(gaps)/len(gaps)
    p_below = 1 - math.exp(-gaps[0]/mean_gap)
    exp_below = p_below*len(gaps)
    print("   memoryless would put " + str(round(exp_below, 1))
          + " of " + str(len(gaps)) + " gaps below " + str(gaps[0])
          + "; we observed 0")
    print("   (that alone is p = " + str(round(math.exp(-exp_below), 3)) + ")")
    print()
    print("   H     P(spike within H | after spike)   | random point    ratio   z")
    starts = [i for i in sp if i < n-600]
    ctl = [i for i in random.sample(range(200, n-600), 5000) if i not in spset]
    for H in (10, 25, 50, 100, 200, 400):
        a = sum(1 for i in starts
                if any((i+k) in spset for k in range(1, H+1)))/len(starts)
        b = sum(1 for i in ctl
                if any((i+k) in spset for k in range(1, H+1)))/len(ctl)
        geo = 1-(1-rate)**H
        se = math.sqrt(a*(1-a)/len(starts) + b*(1-b)/len(ctl))
        z = (a-b)/se if se else 0
        flag = ""
        if z < -3: flag = "  <-- REFRACTORY"
        elif z > 3: flag = "  <-- CLUSTERING"
        print("   " + str(H).rjust(4)
              + ("%.2f%%" % (100*a)).rjust(24)
              + ("%.2f%%" % (100*b)).rjust(16)
              + ("%.2f" % (a/b if b else 0)).rjust(9)
              + ("%+.2f" % z).rjust(7) + flag
              + "   [geometric " + ("%.2f%%" % (100*geo)) + "]")
    print()
    print("   n=" + str(len(starts)) + " post-spike starts, " + str(len(ctl))
          + " controls. A refractory period must show z < -3 at small H.")


run("Boom_1000_Index", True)
run("Crash_1000_Index", False)
run("Boom_500_Index", True)
run("Crash_500_Index", False)
