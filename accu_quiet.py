#!/usr/bin/env python3
"""
accu_quiet.py - can a quiet moment be DETECTED before it matters?

Three direct tests, no inference:

 1. PERSISTENCE. Correlation between volatility of the last W ticks and
    volatility of the NEXT W ticks, for several window sizes. This is the
    whole game: if past quiet does not predict future quiet, no detector
    can exist, however clever.

 2. REGIME EXISTENCE. Shuffle the tick moves - this destroys all time
    structure but keeps the distribution. If real window-volatility is no
    more spread out than shuffled, there are no quiet PERIODS at all,
    only random runs that look like them afterwards.

 3. BEST CASE. Of all moments we could label quiet, how much better is
    the next 100 ticks? Reported as a ratio against a random moment.
"""
import math, os, random, statistics as st, sys

OUT = os.path.expanduser("~/goldlab/data/ticks")
random.seed(31)
DEF = ["Boom_1000_Index", "Crash_1000_Index", "Volatility_75_Index",
       "Volatility_25_Index", "Volatility_10_Index"]


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


def corr(x, y):
    n = len(x)
    if n < 50: return 0.0
    mx, my = sum(x)/n, sum(y)/n
    num = sum((x[i]-mx)*(y[i]-my) for i in range(n))
    dx = math.sqrt(sum((v-mx)**2 for v in x))
    dy = math.sqrt(sum((v-my)**2 for v in y))
    return num/(dx*dy) if dx and dy else 0.0


def winvols(ad, W, step):
    return [sum(ad[i:i+W])/W for i in range(0, len(ad)-W, step)]


def run(sym):
    px = load(sym)
    if len(px) < 20000: return
    ad = [abs(px[i]-px[i-1]) for i in range(1, len(px))]
    print("\n" + "="*60)
    print(sym + "   " + str(len(ad)) + " tick moves")

    print("  1. DOES PAST QUIET PREDICT FUTURE QUIET?")
    for W in (10, 25, 50, 100, 200):
        a, b = [], []
        for i in range(0, len(ad)-2*W, max(1, W//2)):
            a.append(sum(ad[i:i+W])/W)
            b.append(sum(ad[i+W:i+2*W])/W)
        c = corr(a, b)
        verdict = "PREDICTIVE" if abs(c) > 0.10 else "no"
        print("     window " + str(W).rjust(3) + " ticks: corr(past,next) = "
              + ("%+.4f" % c) + "   " + verdict)

    print("  2. DO QUIET PERIODS EXIST AT ALL? (real vs time-shuffled)")
    for W in (50, 200):
        real = winvols(ad, W, W)
        sh = ad[:]; random.shuffle(sh)
        fake = winvols(sh, W, W)
        rs, fs = st.pstdev(real), st.pstdev(fake)
        ratio = rs/fs if fs else 0
        print("     window " + str(W).rjust(3) + ": spread of window-vol real "
              + str(round(rs, 6)) + "  shuffled " + str(round(fs, 6))
              + "   ratio " + str(round(ratio, 3))
              + ("   <-- real regimes" if ratio > 1.15 else "   (1.00 = no regimes)"))

    print("  3. BEST CASE - label the quietest 10% of moments by past 50 ticks,")
    print("     then measure the NEXT 100 ticks:")
    pairs = []
    for i in range(50, len(ad)-100, 7):
        pairs.append((sum(ad[i-50:i])/50, sum(ad[i:i+100])/100))
    pairs.sort()
    k = max(1, len(pairs)//10)
    q_next = sum(p[1] for p in pairs[:k])/k
    l_next = sum(p[1] for p in pairs[-k:])/k
    all_next = sum(p[1] for p in pairs)/len(pairs)
    print("       after quietest 10%: next-100 vol " + str(round(q_next, 6))
          + "  (" + str(round(100*q_next/all_next, 1)) + "% of average)")
    print("       after loudest  10%: next-100 vol " + str(round(l_next, 6))
          + "  (" + str(round(100*l_next/all_next, 1)) + "% of average)")
    gap = abs(q_next - l_next)/all_next
    print("       separation " + str(round(100*gap, 2))
          + "%   " + ("=> usable" if gap > 0.10 else "=> nothing to detect"))


for s in (sys.argv[1:] or DEF):
    run(s)
