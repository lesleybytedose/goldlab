#!/usr/bin/env python3
"""
deriv_digit_tests.py - test whether Deriv tick contracts are beatable.

Pure stdlib. Reads the JSONL produced by deriv_ticks.py.

    python3 deriv_digit_tests.py r75_ticks.jsonl
    python3 deriv_digit_tests.py r75_ticks.jsonl --alpha 0.05 --stake 10

WHY THIS IS DIFFERENT FROM A NORMAL BACKTEST
--------------------------------------------
For gold you need a random control because the null distribution is unknown.
Here the null is known analytically: if Deriv's PRNG is uniform, every digit
is 10%, parity is 50/50, direction is 50/50. So the control is a formula,
not a sample.

The question is never "is the distribution exactly uniform" - no finite
sample is. The question is "does it deviate by more than the house margin,
persistently, out of sample". Those are the thresholds in BREAK_EVEN below.

Every test here is reported against the break-even threshold, not against
the fair-odds value. Beating 10% is meaningless. Beating 11.0% is the bar.
"""

import argparse
import json
import math
import sys
from collections import Counter, defaultdict

# --------------------------------------------------------------------------
# Payout table, taken from the screenshots in the Deriv tick-trading PDF
# (stake = 10 USD). Edit these to match what your account actually quotes;
# payouts drift with volatility and account type.
# --------------------------------------------------------------------------

STAKE = 10.0
PAYOUTS = {
    "rise":        19.53,
    "fall":        19.53,
    "even":        19.61,
    "odd":         19.61,
    "matches":     90.91,   # single specific digit
    "differs":     10.99,
    "over_4":      19.61,   # last digit in {5..9}
    "under_4":     24.39,   # last digit in {0..3}
}
# For barriers with no quoted payout, assume Deriv's standard synthetic margin.
DEFAULT_MARGIN = 0.02


# --------------------------------------------------------------------------
# Statistics (stdlib only)
# --------------------------------------------------------------------------

def norm_sf(z):
    """P(Z > z) for standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _gser(a, x):
    """Series expansion for the regularized lower incomplete gamma P(a,x)."""
    if x <= 0:
        return 0.0
    ap, total, delta = a, 1.0 / a, 1.0 / a
    for _ in range(500):
        ap += 1.0
        delta *= x / ap
        total += delta
        if abs(delta) < abs(total) * 1e-14:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a, x):
    """Continued fraction for the regularized upper incomplete gamma Q(a,x)."""
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b if b != 0 else 1.0 / tiny
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    return h * math.exp(-x + a * math.log(x) - math.lgamma(a))


def chi2_sf(x, df):
    """Upper tail probability of the chi-square distribution."""
    if x <= 0:
        return 1.0
    a, xx = df / 2.0, x / 2.0
    if xx < a + 1.0:
        return 1.0 - _gser(a, xx)
    return _gcf(a, xx)


def prop_z(successes, n, p0):
    """One-sided z-test: is the observed proportion ABOVE p0?"""
    if n == 0:
        return 0.0, 1.0
    phat = successes / n
    se = math.sqrt(p0 * (1.0 - p0) / n)
    if se == 0:
        return 0.0, 1.0
    z = (phat - p0) / se
    return z, norm_sf(z)


def wilson(successes, n, z=1.96):
    """Wilson score interval - well behaved for proportions near 0 or 1."""
    if n == 0:
        return (0.0, 1.0)
    phat = successes / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def benjamini_hochberg(pvals, alpha=0.05):
    """Return the set of indices that survive BH-FDR control."""
    m = len(pvals)
    if m == 0:
        return set()
    order = sorted(range(m), key=lambda i: pvals[i])
    survivors, kmax = set(), -1
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= rank / m * alpha:
            kmax = rank
    if kmax > 0:
        survivors = set(order[:kmax])
    return survivors


def runs_test(seq_bool):
    """Wald-Wolfowitz runs test for serial dependence in a binary sequence."""
    n = len(seq_bool)
    n1 = sum(1 for v in seq_bool if v)
    n2 = n - n1
    if n1 == 0 or n2 == 0 or n < 20:
        return None
    runs = 1 + sum(1 for i in range(1, n) if seq_bool[i] != seq_bool[i - 1])
    mu = 2.0 * n1 * n2 / n + 1.0
    var = (2.0 * n1 * n2 * (2.0 * n1 * n2 - n)) / (n * n * (n - 1.0))
    if var <= 0:
        return None
    z = (runs - mu) / math.sqrt(var)
    return {"runs": runs, "expected": mu, "z": z, "p_two_sided": 2 * norm_sf(abs(z))}


# --------------------------------------------------------------------------
# Break-even thresholds derived from the payout table
# --------------------------------------------------------------------------

def break_even(payout, stake=STAKE):
    """Minimum win probability for EV >= 0."""
    return stake / payout


def payout_for(true_p, margin=DEFAULT_MARGIN, stake=STAKE):
    """Infer a payout for a contract Deriv didn't quote in the PDF."""
    return stake / (true_p * (1.0 + margin))


def ev(win_p, payout, stake=STAKE):
    """Expected profit per trade."""
    return win_p * payout - stake


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------

def load(path):
    ticks = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            ticks.append((int(r["epoch"]), float(r["quote"]), int(r["digit"])))
    ticks.sort(key=lambda t: t[0])
    return ticks


# --------------------------------------------------------------------------
# Reporting helpers
# --------------------------------------------------------------------------

def header(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def verdict(passed):
    return "SIGNAL" if passed else "no signal"


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_margins():
    header("0. HOUSE MARGIN IMPLIED BY DERIV'S OWN PAYOUTS")
    truths = {
        "rise": 0.5, "fall": 0.5, "even": 0.5, "odd": 0.5,
        "matches": 0.1, "differs": 0.9, "over_4": 0.5, "under_4": 0.4,
    }
    print(f"{'contract':<12} {'payout':>8} {'break-even':>11} "
          f"{'fair p':>8} {'margin':>8} {'EV/trade':>10}")
    for name, pay in PAYOUTS.items():
        be = break_even(pay)
        tp = truths[name]
        margin = be / tp - 1.0
        print(f"{name:<12} {pay:>8.2f} {be:>11.5f} {tp:>8.3f} "
              f"{margin:>7.2%} {ev(tp, pay):>10.4f}")
    print()
    print("Read this before anything else. If the stream is uniform, those")
    print("EV numbers are what you earn per trade, guaranteed, forever.")
    print("Note Matches: 10% margin, five times the others. That contract is")
    print("priced to punish exactly the 'I found a hot digit' strategy.")


def test_uniformity(digits, alpha):
    header("1. DIGIT UNIFORMITY (chi-square goodness of fit)")
    n = len(digits)
    counts = Counter(digits)
    expected = n / 10.0
    chi2 = sum((counts.get(d, 0) - expected) ** 2 / expected for d in range(10))
    p = chi2_sf(chi2, 9)

    print(f"n = {n:,}   expected per digit = {expected:,.1f}")
    print()
    print(f"{'digit':>5} {'count':>10} {'freq':>8} {'95% CI':>18} "
          f"{'need':>8} {'gap':>8}")
    be_match = break_even(PAYOUTS["matches"])
    for d in range(10):
        c = counts.get(d, 0)
        f = c / n
        lo, hi = wilson(c, n)
        print(f"{d:>5} {c:>10,} {f:>8.4%} {lo:>8.4%}-{hi:<8.4%} "
              f"{be_match:>8.2%} {f - be_match:>+8.4%}")
    print()
    print(f"chi2 = {chi2:.3f}  df = 9  p = {p:.4f}   -> {verdict(p < alpha)}")
    if p < alpha:
        print("Distribution is not uniform. That alone is NOT an edge -")
        print("check column 'gap': a digit must clear +0 there to be profitable.")
    else:
        print("Consistent with a uniform PRNG.")
    return counts, n


def test_per_digit_matches(counts, n, alpha):
    header("2. IS ANY SINGLE DIGIT PROFITABLE ON 'MATCHES'? (BH-corrected)")
    be = break_even(PAYOUTS["matches"])
    print(f"break-even probability = {be:.4%}  (payout {PAYOUTS['matches']} on {STAKE})")
    print()
    pvals, rows = [], []
    for d in range(10):
        c = counts.get(d, 0)
        z, p = prop_z(c, n, be)
        pvals.append(p)
        rows.append((d, c, c / n, z, p, ev(c / n, PAYOUTS["matches"])))
    keep = benjamini_hochberg(pvals, alpha)
    print(f"{'digit':>5} {'freq':>9} {'z':>8} {'p':>10} {'EV/trade':>10}  BH")
    for i, (d, c, f, z, p, e) in enumerate(rows):
        flag = "PASS" if i in keep else "."
        print(f"{d:>5} {f:>9.4%} {z:>8.3f} {p:>10.4f} {e:>+10.4f}  {flag}")
    print()
    if keep:
        print(f"{len(keep)} digit(s) survive FDR control at alpha={alpha}.")
        print("Do NOT act on this yet - go to test 7 (holdout).")
    else:
        print("No digit clears break-even after multiple-testing correction.")
    return len(keep)


def test_parity(digits, alpha):
    header("3. EVEN / ODD")
    n = len(digits)
    evens = sum(1 for d in digits if d % 2 == 0)
    odds = n - evens
    be = break_even(PAYOUTS["even"])
    lo, hi = wilson(evens, n)
    print(f"even = {evens:,} ({evens/n:.4%})   odd = {odds:,} ({odds/n:.4%})")
    print(f"95% CI on P(even): {lo:.4%} - {hi:.4%}")
    print(f"break-even needed: {be:.4%}")
    print()
    for label, k in (("EVEN", evens), ("ODD", odds)):
        z, p = prop_z(k, n, be)
        print(f"  always-{label:<5} z={z:>7.3f}  p={p:>8.4f}  "
              f"EV/trade={ev(k/n, PAYOUTS['even']):>+8.4f}  -> {verdict(p < alpha)}")
    print()
    r = runs_test([d % 2 == 0 for d in digits])
    if r:
        print(f"Runs test on parity: runs={r['runs']:,} expected={r['expected']:,.1f} "
              f"z={r['z']:.3f} p={r['p_two_sided']:.4f}")
        print(f"  serial dependence in parity -> {verdict(r['p_two_sided'] < alpha)}")


def test_over_under(counts, n, alpha):
    header("4. OVER / UNDER, EVERY BARRIER")
    print("'Over b' wins if last digit > b. 'Under b' wins if last digit < b.")
    print("Payouts for barriers other than 4 are inferred at "
          f"{DEFAULT_MARGIN:.0%} margin - replace with your real quotes.")
    print()
    print(f"{'contract':<10} {'wins':>10} {'freq':>9} {'payout':>8} "
          f"{'break-even':>11} {'EV':>9}")
    results = []
    for b in range(0, 9):
        wins = sum(counts.get(d, 0) for d in range(b + 1, 10))
        true_p = (9 - b) / 10.0
        pay = PAYOUTS.get(f"over_{b}", payout_for(true_p))
        f = wins / n
        results.append((f"over_{b}", wins, f, pay, ev(f, pay)))
    for b in range(1, 10):
        wins = sum(counts.get(d, 0) for d in range(0, b))
        true_p = b / 10.0
        pay = PAYOUTS.get(f"under_{b}", payout_for(true_p))
        f = wins / n
        results.append((f"under_{b}", wins, f, pay, ev(f, pay)))

    pvals = []
    for name, wins, f, pay, e in results:
        be = break_even(pay)
        _, p = prop_z(wins, n, be)
        pvals.append(p)
        print(f"{name:<10} {wins:>10,} {f:>9.4%} {pay:>8.2f} "
              f"{be:>11.4%} {e:>+9.4f}")
    keep = benjamini_hochberg(pvals, alpha)
    print()
    if keep:
        names = ", ".join(results[i][0] for i in sorted(keep))
        print(f"Survives BH-FDR: {names}  -> carry to holdout test.")
    else:
        print("Nothing clears break-even after FDR correction.")


def test_serial_dependence(digits, alpha):
    header("5. SERIAL DEPENDENCE (does the last digit predict the next?)")
    n = len(digits)
    trans = defaultdict(int)
    row_tot, col_tot = Counter(), Counter()
    for i in range(n - 1):
        a, b = digits[i], digits[i + 1]
        trans[(a, b)] += 1
        row_tot[a] += 1
        col_tot[b] += 1
    total = n - 1

    chi2 = 0.0
    for a in range(10):
        for b in range(10):
            exp = row_tot[a] * col_tot[b] / total
            if exp > 0:
                chi2 += (trans[(a, b)] - exp) ** 2 / exp
    p = chi2_sf(chi2, 81)
    print(f"Transition matrix chi-square of independence")
    print(f"  chi2 = {chi2:.2f}  df = 81  p = {p:.4f}  -> {verdict(p < alpha)}")

    print()
    print("Conditional 'Matches' edge: best next-digit bet given current digit")
    be = break_even(PAYOUTS["matches"])
    best = []
    for a in range(10):
        if row_tot[a] < 500:
            continue
        b = max(range(10), key=lambda x: trans[(a, x)])
        f = trans[(a, b)] / row_tot[a]
        _, pv = prop_z(trans[(a, b)], row_tot[a], be)
        best.append((a, b, f, row_tot[a], pv))
    keep = benjamini_hochberg([r[4] for r in best], alpha)
    print(f"{'after':>6} {'bet':>4} {'freq':>9} {'n':>9} {'p':>9}  BH")
    for i, (a, b, f, nn, pv) in enumerate(best):
        print(f"{a:>6} {b:>4} {f:>9.4%} {nn:>9,} {pv:>9.4f}  "
              f"{'PASS' if i in keep else '.'}")
    print()
    print("Note the selection bias: picking the max of 10 per row inflates")
    print("every frequency shown. BH partly corrects it; the holdout settles it.")

    print()
    print("Parity autocorrelation by lag:")
    par = [d % 2 for d in digits]
    mean = sum(par) / len(par)
    var = sum((x - mean) ** 2 for x in par)
    for lag in range(1, 11):
        cov = sum((par[i] - mean) * (par[i + lag] - mean)
                  for i in range(len(par) - lag))
        ac = cov / var if var else 0.0
        se = 1.0 / math.sqrt(len(par))
        mark = "  <-- exceeds 2se" if abs(ac) > 2 * se else ""
        print(f"  lag {lag:>2}: r = {ac:>+8.5f}  (2se = {2*se:.5f}){mark}")


def test_rise_fall(ticks, alpha):
    header("6. RISE / FALL (direction of price, not digits)")
    ups = downs = flats = 0
    dirs = []
    for i in range(1, len(ticks)):
        d = ticks[i][1] - ticks[i - 1][1]
        if d > 0:
            ups += 1
            dirs.append(True)
        elif d < 0:
            downs += 1
            dirs.append(False)
        else:
            flats += 1
    n = ups + downs
    be = break_even(PAYOUTS["rise"])
    print(f"up = {ups:,}  down = {downs:,}  unchanged = {flats:,}")
    print(f"P(up | moved) = {ups/n:.4%}   break-even needed = {be:.4%}")
    lo, hi = wilson(ups, n)
    print(f"95% CI: {lo:.4%} - {hi:.4%}")
    print()
    for label, k in (("RISE", ups), ("FALL", downs)):
        z, p = prop_z(k, n, be)
        print(f"  always-{label:<5} z={z:>7.3f}  p={p:>8.4f}  "
              f"EV/trade={ev(k/n, PAYOUTS['rise']):>+8.4f}  -> {verdict(p < alpha)}")
    print()
    r = runs_test(dirs)
    if r:
        print(f"Runs test on direction: runs={r['runs']:,} "
              f"expected={r['expected']:,.1f} z={r['z']:.3f} "
              f"p={r['p_two_sided']:.4f}")
        print(f"  momentum/mean-reversion in tick direction -> "
              f"{verdict(r['p_two_sided'] < alpha)}")
        if r["p_two_sided"] < alpha:
            print("  (Reminder: the 5-tick contract settles on tick 5 vs tick 0,")
            print("   so 1-tick autocorrelation does not automatically transfer.)")


def test_holdout(digits, ticks, alpha):
    header("7. HOLDOUT: pick the winner in-sample, then pay for it out-of-sample")
    half = len(digits) // 2
    train_d, test_d = digits[:half], digits[half:]
    print(f"train = {len(train_d):,} ticks   test = {len(test_d):,} ticks")
    print()

    strategies = []

    # best single digit for Matches
    tc = Counter(train_d)
    best_digit = max(range(10), key=lambda d: tc.get(d, 0))
    strategies.append((
        f"Matches digit {best_digit}",
        lambda d, bd=best_digit: d == bd,
        PAYOUTS["matches"],
    ))

    # even vs odd
    ev_train = sum(1 for d in train_d if d % 2 == 0) / len(train_d)
    if ev_train >= 0.5:
        strategies.append(("Always EVEN", lambda d: d % 2 == 0, PAYOUTS["even"]))
    else:
        strategies.append(("Always ODD", lambda d: d % 2 == 1, PAYOUTS["odd"]))

    # best over/under barrier by in-sample EV
    best = None
    for b in range(9):
        wins = sum(1 for d in train_d if d > b)
        pay = PAYOUTS.get(f"over_{b}", payout_for((9 - b) / 10.0))
        e = ev(wins / len(train_d), pay)
        if best is None or e > best[0]:
            best = (e, f"Over {b}", (lambda d, bb=b: d > bb), pay)
    for b in range(1, 10):
        wins = sum(1 for d in train_d if d < b)
        pay = PAYOUTS.get(f"under_{b}", payout_for(b / 10.0))
        e = ev(wins / len(train_d), pay)
        if best is None or e > best[0]:
            best = (e, f"Under {b}", (lambda d, bb=b: d < bb), pay)
    strategies.append((best[1], best[2], best[3]))

    print(f"{'strategy':<20} {'in-sample EV':>13} {'out-of-sample':>14} "
          f"{'95% CI on EV':>22}")
    for name, fn, pay in strategies:
        tr_w = sum(1 for d in train_d if fn(d))
        te_w = sum(1 for d in test_d if fn(d))
        tr_ev = ev(tr_w / len(train_d), pay)
        te_p = te_w / len(test_d)
        te_ev = ev(te_p, pay)
        lo, hi = wilson(te_w, len(test_d))
        print(f"{name:<20} {tr_ev:>+13.4f} {te_ev:>+14.4f} "
              f"{ev(lo, pay):>+10.4f} to {ev(hi, pay):>+8.4f}")

    print()
    print("The in-sample column will almost always look better than the")
    print("out-of-sample one. That gap is the cost of searching. If the")
    print("out-of-sample CI includes zero, you have nothing.")


def power_note(n, alpha):
    header("8. WHAT COULD THIS SAMPLE EVEN DETECT?")
    print(f"n = {n:,}")
    print()
    print("Minimum detectable true probability at 80% power, one-sided "
          f"alpha={alpha}:")
    z_a, z_b = 1.645, 0.8416
    for label, p0 in (("Matches (need >11.00%)", break_even(PAYOUTS["matches"])),
                      ("Even/Odd (need >50.99%)", break_even(PAYOUTS["even"])),
                      ("Rise/Fall (need >51.20%)", break_even(PAYOUTS["rise"]))):
        se = math.sqrt(p0 * (1 - p0) / n)
        mdp = p0 + (z_a + z_b) * se
        print(f"  {label:<26} detectable if true rate >= {mdp:.4%}")
    print()
    print("If those numbers are far above the break-even points, the sample")
    print("is too small to rule an edge in or out. Collect more ticks.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--stake", type=float, default=10.0)
    a = ap.parse_args()

    global STAKE
    STAKE = a.stake

    ticks = load(a.path)
    if len(ticks) < 1000:
        print(f"Only {len(ticks)} ticks - too few for any of this. "
              "Collect at least 20,000.", file=sys.stderr)
        sys.exit(1)
    digits = [t[2] for t in ticks]

    print(f"Loaded {len(ticks):,} ticks from {a.path}")
    print(f"Span: epoch {ticks[0][0]} -> {ticks[-1][0]} "
          f"({(ticks[-1][0]-ticks[0][0])/3600:.1f} hours)")

    test_margins()
    counts, n = test_uniformity(digits, a.alpha)
    test_per_digit_matches(counts, n, a.alpha)
    test_parity(digits, a.alpha)
    test_over_under(counts, n, a.alpha)
    test_serial_dependence(digits, a.alpha)
    test_rise_fall(ticks, a.alpha)
    test_holdout(digits, ticks, a.alpha)
    power_note(n, a.alpha)

    header("HOW TO READ THE WHOLE THING")
    print("An edge exists only if ALL of these hold:")
    print("  1. a contract clears break-even, not just fair odds")
    print("  2. it survives BH-FDR correction across all contracts tested")
    print("  3. it holds up in test 7 out-of-sample")
    print("  4. the out-of-sample CI excludes zero")
    print("  5. it repeats on a freshly collected sample, days later")
    print()
    print("Fail any one and the honest answer is 'no edge found'.")


if __name__ == "__main__":
    main()
