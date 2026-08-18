#!/usr/bin/env python3
"""
accu_sim.py - can ANY signal help a 3% accumulator?

Three tests, in order of decisiveness:

 1. CLUSTERING. Autocorrelation of |tick move|. The barrier is checked
    tick-to-tick, so a signal can only help if quiet ticks predict more
    quiet ticks. ACF ~ 0 => no signal of any kind can work. Ever.

 2. SURVIVAL BASELINE. Simulate accumulators from random start ticks at
    the fair barrier. Confirms the simulator matches theory.

 3. CONDITIONED SURVIVAL. Does starting in the quietest 20% of moments
    (or right after a spike) beat starting at random? This is the
    accumulator version of RANDOM CONTROL.

  python3 accu_sim.py [--growth 0.03] [--barrier X] [SYMBOL ...]
"""
import math, os, random, statistics as st, sys

OUT = os.path.expanduser("~/goldlab/data/ticks")
random.seed(17)
DEF = ["Boom_1000_Index", "Crash_1000_Index", "Volatility_75_Index",
       "Volatility_25_Index", "Volatility_10_Index"]


def load(s):
    p = os.path.join(OUT, s + "_ticks.csv")
    if not os.path.exists(p):
        return []
    r = []
    for ln in open(p):
        a = ln.strip().split(",")
        if len(a) == 2 and a[0].isdigit():
            try: r.append(float(a[1]))
            except Exception: pass
    return r


def acf(x, lag):
    n = len(x) - lag
    if n < 100: return 0.0
    m = sum(x) / len(x)
    num = sum((x[i] - m) * (x[i + lag] - m) for i in range(n))
    den = sum((v - m) ** 2 for v in x)
    return num / den if den else 0.0


def quant(xs, q):
    s = sorted(xs); return s[int(q * (len(s) - 1))]


def survive(ad, start, barrier, cap=230):
    """ticks survived from start, barrier checked each tick"""
    n = 0
    for j in range(start, min(start + cap, len(ad))):
        if ad[j] > barrier:
            return n
        n += 1
    return n


def run(sym, growth, forced=None):
    px = load(sym)
    if len(px) < 20000:
        return
    d = [px[i] - px[i - 1] for i in range(1, len(px))]
    ad = [abs(x) for x in d]
    med = st.median(ad)
    print("\n" + "=" * 62)
    print(sym + "   " + str(len(px)) + " ticks   growth " + str(int(growth * 100)) + "%")

    # ---- 1. clustering
    print("  1. VOLATILITY CLUSTERING (ACF of |tick move|)")
    strong = False
    for lag in (1, 5, 20, 50):
        a = acf(ad, lag)
        if abs(a) > 0.05: strong = True
        print("     lag " + str(lag).rjust(3) + ": " + ("%+.4f" % a))
    print("     => " + ("clustering present - a signal COULD help"
                        if strong else
                        "NO clustering - no signal can change survival odds"))

    # ---- 2. baseline
    fair = quant(ad, 1.0 / (1.0 + growth))
    bar = forced if forced else fair
    label = "forced " + str(bar) if forced else "fair " + str(round(fair, 5))
    print("  2. SURVIVAL AT BARRIER " + label)
    starts = [random.randrange(200, len(ad) - 250) for _ in range(4000)]
    surv = [survive(ad, s, bar) for s in starts]
    mean_t = sum(surv) / len(surv)
    print("     mean ticks survived " + str(round(mean_t, 1))
          + "   median " + str(int(st.median(surv))))
    print("     cash-out plan   P(reach)   payout x   EV per $1 staked")
    for n in (10, 25, 55, 75, 230):
        p = sum(1 for t in surv if t >= n) / len(surv)
        mult = (1 + growth) ** n
        print("       at " + str(n).rjust(3) + " ticks   "
              + (str(round(100 * p, 2)) + "%").rjust(7) + "   "
              + str(round(mult, 2)).rjust(8) + "x   "
              + str(round(p * mult, 4)).rjust(8)
              + ("  <-- positive" if p * mult > 1.02 else ""))
    ev = max(sum(1 for t in surv if t >= n) / len(surv) * (1 + growth) ** n
             for n in range(5, 231))
    print("     best cash-out EV over all N: " + str(round(ev, 4))
          + "   (1.0000 = fair; break = stake lost, so this is the whole story)")

    # ---- 3. conditioned
    print("  3. CAN A SIGNAL BEAT A RANDOM START?")
    vol50 = []
    for i in range(200, len(ad) - 250):
        vol50.append((sum(ad[i - 50:i]) / 50, i))
    vol50.sort()
    quiet = [i for _, i in vol50[:len(vol50) // 5]]
    loud = [i for _, i in vol50[-len(vol50) // 5:]]
    def bestev(sv):
        return max(sum(1 for t in sv if t >= n) / len(sv) * (1 + growth) ** n
                   for n in range(5, 231))
    for name, pool in (("quietest 20%", quiet), ("loudest 20%", loud)):
        s = random.sample(pool, min(3000, len(pool)))
        sv = [survive(ad, i, bar) for i in s]
        print("     " + name.ljust(13) + " mean ticks "
              + str(round(sum(sv) / len(sv), 1)).rjust(6)
              + "   best EV " + str(round(bestev(sv), 4)))
    sp = [i for i, x in enumerate(ad) if x > 10 * med]
    if len(sp) >= 30:
        after = [i + 1 for i in sp if 200 < i < len(ad) - 250]
        sv = [survive(ad, i, bar) for i in after]
        print("     after a spike mean ticks "
              + str(round(sum(sv) / len(sv), 1)).rjust(6)
              + "   best EV " + str(round(bestev(sv), 4))
              + "   (n=" + str(len(sv)) + ")")
    print("     baseline      mean ticks " + str(round(mean_t, 1)).rjust(6)
          + "   best EV " + str(round(ev, 4)))


def main():
    args = sys.argv[1:]
    growth, forced = 0.03, None
    if "--growth" in args:
        i = args.index("--growth"); growth = float(args[i + 1]); del args[i:i + 2]
    if "--barrier" in args:
        i = args.index("--barrier"); forced = float(args[i + 1]); del args[i:i + 2]
    for s in (args or DEF):
        run(s, growth, forced)


main()
