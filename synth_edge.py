#!/usr/bin/env python3
"""
synth_edge.py - deep edge hunt across every synthetic we feed.

Tests, in order of how much an edge would be worth:

 1. SIGNED AUTOCORRELATION. Do ticks trend or revert? This governs every
    DIRECTIONAL model. (We previously only tested |moves|, which governs
    accumulators. This is the untested half.)
 2. MARTINGALE / DRIFT. Is the mean tick move zero, with a proper
    confidence interval? Any real drift is a free directional edge.
 3. SYMMETRY. P(up) vs P(down), mean up-move vs mean down-move. For
    Boom/Crash the answer is structurally lopsided - the question is
    whether it nets to zero.
 4. RUNS. Are up/down sequences shorter or longer than coin-flip? A
    classic reversal/momentum tell that ACF can miss.
 5. THE HURDLE. What each trade actually costs in spread, expressed in R,
    per symbol. This is the number any model must beat.
"""
import math, os, statistics as st, sys

TICKS = os.path.expanduser("~/goldlab/data/ticks")
RAW = os.path.expanduser("~/goldlab/data/raw")
ALL = ["Boom_1000_Index", "Crash_1000_Index", "Boom_500_Index",
       "Crash_500_Index", "Volatility_75_Index", "Volatility_100_Index",
       "Volatility_25_Index", "Volatility_50_Index", "Volatility_10_Index"]


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


def acf(x, lag):
    n = len(x) - lag
    if n < 100: return 0.0, 0.0
    m = sum(x)/len(x)
    num = sum((x[i]-m)*(x[i+lag]-m) for i in range(n))
    den = sum((v-m)**2 for v in x)
    r = num/den if den else 0.0
    se = 1.0/math.sqrt(len(x))            # ~SE under the null
    return r, r/se if se else 0.0


def spread_hurdle(sym):
    p = os.path.join(RAW, sym + "_M15_live.csv")
    if not os.path.exists(p): return None
    sp, rng = [], []
    lines = open(p).read().splitlines()[1:]
    for ln in lines[-2000:]:
        a = ln.split(",")
        if len(a) < 7: continue
        try:
            h, l, s = float(a[3]), float(a[4]), float(a[6])
            if s > 0: sp.append(s); rng.append(h-l)
        except Exception: pass
    if not sp: return None
    return st.median(sp), st.median(rng)


def run(sym):
    px = load(sym)
    if len(px) < 20000:
        return
    d = [px[i]-px[i-1] for i in range(1, len(px))]
    n = len(d)
    mean = sum(d)/n
    sd = st.pstdev(d)
    se = sd/math.sqrt(n)
    print("\n" + "="*68)
    print(sym + "   " + str(n) + " ticks   last " + str(round(px[-1], 2)))

    print("  1. SIGNED AUTOCORRELATION  (directional predictability)")
    hit = False
    for lag in (1, 2, 3, 5, 10, 50):
        r, t = acf(d, lag)
        flag = ""
        if abs(t) > 3.0:
            flag = "   <-- " + ("MOMENTUM" if r > 0 else "REVERSION") + " t=" + str(round(t,1))
            hit = True
        print("     lag " + str(lag).rjust(2) + ": r = " + ("%+.5f" % r)
              + "   t = " + ("%+.2f" % t) + flag)
    print("     => " + ("SOMETHING THERE - investigate" if hit
                        else "no directional structure at tick level"))

    print("  2. DRIFT  (is it a martingale?)")
    print("     mean tick move " + ("%+.6f" % mean)
          + "   95% CI [" + ("%+.6f" % (mean-1.96*se))
          + ", " + ("%+.6f" % (mean+1.96*se)) + "]")
    per1k = 100*mean*1000/px[-1]
    lo = 100*(mean-1.96*se)*1000/px[-1]
    hi = 100*(mean+1.96*se)*1000/px[-1]
    sig = "SIGNIFICANT DRIFT" if (mean-1.96*se)*(mean+1.96*se) > 0 else "no drift detectable"
    print("     = " + ("%+.4f" % per1k) + "% per 1000 ticks   CI ["
          + ("%+.4f" % lo) + ", " + ("%+.4f" % hi) + "]   " + sig)

    print("  3. SYMMETRY")
    up = [x for x in d if x > 0]; dn = [x for x in d if x < 0]
    fu = len(up)/n
    mu = sum(up)/len(up) if up else 0
    md = sum(dn)/len(dn) if dn else 0
    print("     P(up) " + str(round(100*fu, 2)) + "%   mean up "
          + str(round(mu, 6)) + "   mean down " + str(round(md, 6)))
    print("     up-share of moves x mean up = " + ("%+.6f" % (fu*mu))
          + "   down = " + ("%+.6f" % ((1-fu-(1-fu-len(dn)/n))*md if False else (len(dn)/n)*md)))

    print("  4. RUNS  (streaks vs coin flip)")
    signs = [1 if x > 0 else -1 for x in d if x != 0]
    runs = 1 + sum(1 for i in range(1, len(signs)) if signs[i] != signs[i-1])
    npos = sum(1 for s in signs if s > 0); nneg = len(signs)-npos
    exp_runs = 2*npos*nneg/len(signs) + 1
    var = (exp_runs-1)*(exp_runs-2)/max(1, len(signs)-1)
    z = (runs-exp_runs)/math.sqrt(var) if var > 0 else 0
    print("     runs " + str(runs) + "   expected " + str(round(exp_runs, 1))
          + "   z = " + ("%+.2f" % z)
          + ("   <-- structure" if abs(z) > 3 else "   (coin-flip-like)"))

    h = spread_hurdle(sym)
    if h:
        med_sp, med_rng = h
        print("  5. COST HURDLE  (from the M15 feed)")
        print("     median spread " + str(round(med_sp, 4))
              + "   median M15 bar range " + str(round(med_rng, 4)))
        for atr_mult in (1.0,):
            risk = med_rng * atr_mult
            if risk > 0:
                print("     a 1-bar-range stop costs "
                      + str(round(100*med_sp/risk, 2))
                      + "% of R in spread per trade")
                print("     => a model needs > +"
                      + str(round(med_sp/risk, 4))
                      + "R edge just to break even")


for s in (sys.argv[1:] or ALL):
    run(s)
