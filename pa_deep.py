#!/usr/bin/env python3
"""
pa_deep.py - price action on synthetics, tested at the level it is claimed.

 1. POST-SPIKE PATH. After a spike, does price retrace it, hold it, or
    continue? Measured over 1..500 ticks against random-point controls.
    An ACF cannot see this: spikes are 0.1% of ticks.

 2. IS BOOM RANDOM BETWEEN SPIKES? If non-spike ticks are near-constant,
    the chart is a straight line and every zone/trendline drawn on it is
    an artifact of where spikes landed.

 3. PULLBACK / RUNS. After k consecutive moves one way, what does the
    next move do? The core price-action claim, stated testably.

 4. DOES IID SURVIVE AGGREGATION? ACF of returns at tick, 10-tick,
    60-tick and real M15 bars - the scale the models actually trade.
"""
import math, os, random, statistics as st, sys

TICKS = os.path.expanduser("~/goldlab/data/ticks")
RAW = os.path.expanduser("~/goldlab/data/raw")
random.seed(808)


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


def bars(sym):
    p = os.path.join(RAW, sym + "_M15_live.csv")
    if not os.path.exists(p): return []
    out = []
    for ln in open(p).read().splitlines()[1:]:
        a = ln.split(",")
        if len(a) >= 6:
            try: out.append(float(a[5]))
            except Exception: pass
    return out


def acf(x, lag):
    n = len(x) - lag
    if n < 200: return None, None
    m = sum(x)/len(x)
    num = sum((x[i]-m)*(x[i+lag]-m) for i in range(n))
    den = sum((v-m)**2 for v in x)
    r = num/den if den else 0.0
    return r, r*math.sqrt(len(x))


def post_spike(sym, up=True):
    px = load(sym)
    if len(px) < 20000: return
    d = [px[i]-px[i-1] for i in range(1, len(px))]
    med = st.median([abs(x) for x in d])
    sp = [i for i, x in enumerate(d) if (x > 10*med if up else x < -10*med)]
    sp = [i for i in sp if 200 < i < len(d)-600]
    if len(sp) < 30: return
    size = st.median([abs(d[i]) for i in sp])
    print("\n" + "="*66)
    print("1. POST-SPIKE PATH  " + sym + "   " + str(len(sp))
          + " spikes, median size " + str(round(size, 3)))
    ctl = random.sample(range(200, len(d)-600), 2000)
    print("   horizon    move after spike     control      as % of spike")
    for H in (1, 5, 10, 25, 50, 100, 250, 500):
        a = [px[i+1+H] - px[i+1] for i in sp]      # from just AFTER the spike
        b = [px[i+1+H] - px[i+1] for i in ctl]
        ma, mb = sum(a)/len(a), sum(b)/len(b)
        se = st.pstdev(a)/math.sqrt(len(a)) if len(a) > 2 else 0
        t = (ma-mb)/se if se else 0
        pct = 100*(ma-mb)/size if size else 0
        flag = "  <-- t=" + str(round(t, 1)) if abs(t) > 3 else ""
        print("   " + str(H).rjust(6) + "  " + ("%+.3f" % ma).rjust(12)
              + ("%+.3f" % mb).rjust(13) + ("%+.1f%%" % pct).rjust(15) + flag)
    print("   (retrace would show a large NEGATIVE % for an up-spike)")


def between_spikes(sym):
    px = load(sym)
    if len(px) < 20000: return
    d = [px[i]-px[i-1] for i in range(1, len(px))]
    med = st.median([abs(x) for x in d])
    small = [x for x in d if abs(x) <= 10*med]
    print("\n" + "="*66)
    print("2. IS " + sym + " RANDOM BETWEEN SPIKES?")
    m, s = sum(small)/len(small), st.pstdev(small)
    print("   " + str(len(small)) + " non-spike ticks")
    print("   mean " + str(round(m, 5)) + "   sd " + str(round(s, 5))
          + "   CV = " + str(round(abs(s/m), 3)) if m else "")
    uniq = len(set(round(x, 4) for x in small))
    print("   distinct values (4dp): " + str(uniq))
    up = sum(1 for x in small if x > 0)
    print("   direction: " + str(round(100*up/len(small), 2)) + "% up, "
          + str(round(100*(len(small)-up)/len(small), 2)) + "% down")
    if abs(s/m) < 0.35 if m else False:
        print("   => NEARLY DETERMINISTIC between spikes. Zones and trendlines")
        print("      drawn on this chart describe where spikes landed, nothing more.")
    else:
        print("   => genuinely random between spikes")


def pullback(sym):
    px = load(sym)
    if len(px) < 20000: return
    d = [px[i]-px[i-1] for i in range(1, len(px))]
    print("\n" + "="*66)
    print("3. PULLBACK / RUNS  " + sym)
    print("   after k moves the same way, what does the next move do?")
    print("   k    n         P(reverse)    expected     z")
    sign = [1 if x > 0 else (-1 if x < 0 else 0) for x in d]
    base_up = sum(1 for s in sign if s > 0)/sum(1 for s in sign if s != 0)
    for k in (1, 2, 3, 4, 5, 8):
        rev = tot = 0
        run = 0; last = 0
        for i, s in enumerate(sign):
            if s == 0: continue
            if s == last: run += 1
            else: run = 1; last = s
            if run == k and i+1 < len(sign) and sign[i+1] != 0:
                tot += 1
                if sign[i+1] != s: rev += 1
        if tot < 50: continue
        p = rev/tot
        exp = (1-base_up) if last > 0 else base_up
        exp = 0.5 if abs(base_up-0.5) < 0.02 else exp
        se = math.sqrt(0.25/tot)
        z = (p-0.5)/se
        flag = "  <-- z=" + str(round(z, 1)) if abs(z) > 3 else ""
        print("   " + str(k).rjust(2) + str(tot).rjust(8)
              + ("%.2f%%" % (100*p)).rjust(14)
              + "      50.00%" + ("%+.2f" % z).rjust(9) + flag)
    print("   (P(reverse) far from 50% = real runs structure. Note Boom/Crash")
    print("    are lopsided by design, so read z with that in mind.)")


def scales(sym, m15sym=None):
    px = load(sym)
    if len(px) < 20000: return
    print("\n" + "="*66)
    print("4. DOES INDEPENDENCE SURVIVE AGGREGATION?  " + sym)
    print("   scale          lag-1 r      t        verdict")
    for step in (1, 10, 60, 300):
        agg = [px[i] - px[i-step] for i in range(step, len(px), step)]
        r, t = acf(agg, 1)
        if r is None: continue
        v = "structure" if abs(t) > 3 else "independent"
        print("   " + (str(step) + "-tick").ljust(14)
              + ("%+.5f" % r).rjust(9) + ("%+.2f" % t).rjust(9) + "   " + v)
    if m15sym:
        b = bars(m15sym)
        if len(b) > 500:
            rets = [b[i]-b[i-1] for i in range(1, len(b))]
            r, t = acf(rets, 1)
            if r is not None:
                v = "structure" if abs(t) > 3 else "independent"
                print("   " + "M15 bars".ljust(14) + ("%+.5f" % r).rjust(9)
                      + ("%+.2f" % t).rjust(9) + "   " + v
                      + "   (" + str(len(rets)) + " bars)")


if __name__ == "__main__":
    post_spike("Boom_1000_Index", up=True)
    post_spike("Crash_1000_Index", up=False)
    between_spikes("Boom_1000_Index")
    between_spikes("Crash_1000_Index")
    pullback("Volatility_75_Index")
    pullback("Boom_1000_Index")
    scales("Volatility_75_Index", "Volatility_75_Index")
    scales("Boom_1000_Index", "Boom_1000_Index")
