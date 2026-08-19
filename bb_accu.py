#!/usr/bin/env python3
"""
bb_accu.py - the Bollinger Band accumulator strategy, tested as published.

The rule, as circulated (Scribd "Accumulator Strategy Bollinger MACD",
echoed in Stanzione's tips): enter when Bollinger Bands are CONTRACTED and
the MACD histogram is FLAT; avoid when bands are expanding or momentum is
strong. Growth 1-3%, hold 1-3 minutes.

Implemented literally on tick data:
  BB(20, 2) on ticks; bandwidth = (upper-lower)/mid
  squeeze  = bandwidth in the bottom Nth percentile of the last 500 ticks
  flat MACD = |hist(12,26,9)| in the bottom Nth percentile

Then: open an accumulator at that tick and measure how long it survives
against the SAME rule applied at random ticks. If the strategy works,
squeeze entries must survive materially longer.
"""
import math, os, random, statistics as st, sys

TICKS = os.path.expanduser("~/goldlab/data/ticks")
random.seed(31337)
SYMS = ["1HZ100V", "Volatility_75_Index", "Volatility_100_Index",
        "Volatility_25_Index", "Volatility_10_Index", "Boom_1000_Index"]
GROWTH = 0.03


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


def ema_series(v, n):
    k = 2.0/(n+1); out = [v[0]]
    for x in v[1:]: out.append(x*k + out[-1]*(1-k))
    return out


def q(xs, p):
    s = sorted(xs); return s[min(len(s)-1, int(p*(len(s)-1)))]


def survive(ad, start, barrier, cap=230):
    n = 0
    for j in range(start, min(start+cap, len(ad))):
        if ad[j] > barrier: return n
        n += 1
    return n


def run(sym):
    px = load(sym)
    if len(px) < 30000: return
    ad = [abs(px[i]-px[i-1]) for i in range(1, len(px))]
    fair = q(ad, 1.0/(1.0+GROWTH))          # fair barrier for 3% growth

    # Bollinger bandwidth on ticks
    N = 20
    bw = [None]*len(px)
    for i in range(N, len(px)):
        w = px[i-N+1:i+1]
        m = sum(w)/N
        sd = (sum((x-m)**2 for x in w)/N) ** 0.5
        bw[i] = (4*sd)/m if m else 0

    # MACD histogram
    e12 = ema_series(px, 12); e26 = ema_series(px, 26)
    macd = [a-b for a, b in zip(e12, e26)]
    sig = ema_series(macd, 9)
    hist = [abs(a-b) for a, b in zip(macd, sig)]

    print("\n" + "="*66)
    print(sym + "   " + str(len(px)) + " ticks   fair barrier "
          + ("%.6f" % fair) + "   growth " + str(int(GROWTH*100)) + "%")

    idx = [i for i in range(600, len(px)-260) if bw[i] is not None]
    ctl = random.sample(idx, min(4000, len(idx)))
    base = [survive(ad, i, fair) for i in ctl]
    mb = sum(base)/len(base)

    print("   entry rule                 n     mean ticks    vs random    EV/$1")
    def report(name, pts):
        if len(pts) < 100:
            print("   " + name.ljust(26) + str(len(pts)).rjust(5) + "   too few")
            return
        s = [survive(ad, i, fair) for i in random.sample(pts, min(4000, len(pts)))]
        ms = sum(s)/len(s)
        ev = max(sum(1 for t in s if t >= k)/len(s) * (1+GROWTH)**k
                 for k in range(1, 231))
        print("   " + name.ljust(26) + str(len(pts)).rjust(5)
              + ("%.1f" % ms).rjust(13) + ("%+.1f%%" % (100*(ms/mb-1))).rjust(13)
              + ("%.3f" % ev).rjust(9))

    report("random entry (control)", ctl)
    for pct in (0.10, 0.20, 0.33):
        sq = []
        for i in idx:
            w = [bw[k] for k in range(i-500, i) if bw[k] is not None]
            if not w: continue
            if bw[i] <= q(w, pct): sq.append(i)
            if len(sq) > 6000: break
        report("BB squeeze bottom " + str(int(pct*100)) + "%", sq)
    # squeeze AND flat MACD, as the strategy specifies
    both = []
    for i in idx:
        w = [bw[k] for k in range(i-500, i) if bw[k] is not None]
        hw = hist[i-500:i]
        if not w or not hw: continue
        if bw[i] <= q(w, 0.20) and hist[i] <= q(hw, 0.20): both.append(i)
        if len(both) > 6000: break
    report("squeeze + flat MACD", both)
    # the opposite, as a sanity check: expansion should be WORSE if the rule works
    exp = []
    for i in idx:
        w = [bw[k] for k in range(i-500, i) if bw[k] is not None]
        if not w: continue
        if bw[i] >= q(w, 0.80): exp.append(i)
        if len(exp) > 6000: break
    report("BB expansion (avoid)", exp)
    print("   EV 1.000 = fair. The strategy claims squeeze >> random and")
    print("   expansion << random. If all three match, the rule does nothing.")


for s in (sys.argv[1:] or SYMS):
    run(s)
