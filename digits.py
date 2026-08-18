#!/usr/bin/env python3
"""
digits.py - the untouched attack surface: LAST-DIGIT trade types.

Matches/Differs, Over/Under and Even/Odd do not pay on price movement.
They pay on the last digit of the quote. Everything we proved about the
price being an IID martingale says NOTHING about digit uniformity.

Deriv's payouts on these assume each digit appears exactly 10% of the
time. If the generator's construction or rounding skews that even
slightly, it is a mechanical edge - no prediction, no timing.

Tests, per symbol:
 1. DIGIT FREQUENCY vs uniform, with a chi-square test.
 2. EVEN/ODD split, with a binomial test.
 3. OVER/UNDER at each threshold (the actual contract cut points).
 4. SERIAL DEPENDENCE - does this digit predict the next one?
 5. IMPLIED EDGE at Deriv's published payout ratios.

CAVEAT printed at the end: precision handling matters, and must be
verified against a live quote before any of this is believed.
"""
import math, os, statistics as st, sys
from collections import Counter

TICKS = os.path.expanduser("~/goldlab/data/ticks")
SYMS = ["1HZ100V", "Volatility_75_Index", "Volatility_100_Index",
        "Volatility_25_Index", "Volatility_50_Index", "Volatility_10_Index",
        "Boom_1000_Index", "Crash_1000_Index",
        "Boom_500_Index", "Crash_500_Index"]


def load_raw(s):
    """keep the price as TEXT so the quoted digits survive"""
    p = os.path.join(TICKS, s + "_ticks.csv")
    if not os.path.exists(p): return []
    out = []
    for ln in open(p):
        a = ln.strip().split(",")
        if len(a) == 2 and a[0].isdigit():
            out.append(a[1])
    return out


def chi2_p(chi2, df):
    """upper tail of chi-square, via a decent approximation"""
    if df <= 0: return 1.0
    # Wilson-Hilferty
    z = ((chi2/df)**(1/3.0) - (1 - 2.0/(9*df))) / math.sqrt(2.0/(9*df))
    return 0.5 * math.erfc(z/math.sqrt(2))


def run(sym):
    raw = load_raw(sym)
    if len(raw) < 20000: return
    dps = Counter(len(x.split(".")[1]) if "." in x else 0 for x in raw)
    main_dp = dps.most_common(1)[0][0]
    # use only quotes at the symbol's normal precision
    use = [x for x in raw if (len(x.split(".")[1]) if "." in x else 0) == main_dp]
    digits = [int(x[-1]) for x in use]
    n = len(digits)
    if n < 10000: return
    c = Counter(digits)
    print("\n" + "="*66)
    print(sym + "   " + str(n) + " quotes at " + str(main_dp) + " decimals"
          + ("   [WARNING: mixed precision, " + str(len(dps)) + " lengths]"
             if len(dps) > 1 else ""))

    exp = n/10.0
    chi2 = sum((c.get(d, 0)-exp)**2/exp for d in range(10))
    p = chi2_p(chi2, 9)
    print("   1. DIGIT FREQUENCY  (expected " + str(round(exp)) + " each)")
    line = "      "
    for d in range(10):
        pct = 100*c.get(d, 0)/n
        line += str(d) + ":" + ("%.2f%%" % pct) + "  "
        if d == 4: line += "\n      "
    print(line)
    print("      chi-square = " + str(round(chi2, 1)) + " (df 9)   p = "
          + ("%.4g" % p)
          + ("   <-- NOT UNIFORM" if p < 0.001 else "   uniform"))

    ev = sum(1 for d in digits if d % 2 == 0)
    se = math.sqrt(0.25*n)
    z = (ev - n/2)/se
    print("   2. EVEN/ODD   even " + ("%.3f%%" % (100*ev/n))
          + "   z = " + ("%+.2f" % z)
          + ("   <-- SKEWED" if abs(z) > 3.3 else ""))

    print("   3. OVER/UNDER  (contract cut points)")
    for cut in range(1, 9):
        over = sum(1 for d in digits if d > cut)/n
        fair = (9-cut)/10.0
        zz = (over-fair)/math.sqrt(fair*(1-fair)/n)
        flag = "  <--" if abs(zz) > 3.3 else ""
        print("      over " + str(cut) + ": " + ("%.3f%%" % (100*over))
              + "   fair " + ("%.1f%%" % (100*fair))
              + "   z " + ("%+.2f" % zz) + flag)

    pairs = Counter((digits[i], digits[i+1]) for i in range(n-1))
    chi2b = 0.0
    expb = (n-1)/100.0
    for a in range(10):
        for b in range(10):
            chi2b += (pairs.get((a, b), 0)-expb)**2/expb
    pb = chi2_p(chi2b, 81)
    print("   4. SERIAL DEPENDENCE  chi-square = " + str(round(chi2b, 1))
          + " (df 81)   p = " + ("%.4g" % pb)
          + ("   <-- DIGITS PREDICT DIGITS" if pb < 0.001 else "   independent"))

    best = max(range(10), key=lambda d: c.get(d, 0))
    bp = c.get(best, 0)/n
    print("   5. IMPLIED EDGE   most frequent digit " + str(best)
          + " at " + ("%.3f%%" % (100*bp)))
    for payout in (9.0, 9.5):
        e = bp*payout - 1
        print("      Matches at " + str(payout) + "x payout -> EV "
              + ("%+.4f" % e) + " per $1"
              + ("   ** POSITIVE **" if e > 0.01 else ""))


for s in SYMS:
    run(s)

print("\n" + "="*66)
print("CAVEATS - read before believing any of this:")
print(" - our stored prices came through a float conversion. If Deriv quotes")
print("   more decimals than survived that, the 'last digit' here is not the")
print("   contract's digit. Verify against a live quote before acting.")
print(" - 10 symbols x ~13 tests = multiple comparisons. Only p < 0.0005")
print("   should count, and it must replicate on a second sample.")
