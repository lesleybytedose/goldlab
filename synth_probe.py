#!/usr/bin/env python3
"""
synth_probe.py - generator diagnostics for Deriv synthetics.

WHY THIS EXISTS
  Chart models failed on synthetics because a generated index has no
  sessions, no participants and no order flow. Accumulators are negative
  EV by barrier arithmetic. Spike timing is memoryless. What is left is
  the only honest question about a KNOWN generator:

      does the series deviate from the process it claims to be?

  If it does not, no entry rule can win and we stop. If it does, the
  deviation IS the model - and it must then be pre-registered and
  forward-tested like anything else.

WHAT IS MEASURED  (all from stored bars, read-only)
  1. bleed/spike arithmetic - do the drift and the jumps net to zero?
     This is the decisive test for Boom/Crash. If the bleed exactly pays
     for the spikes, no configuration of entries or exits can win.
  2. hazard rate       - is spike timing really memoryless? P(spike now
                         | none yet) should be FLAT against elapsed time.
  3. spike size vs wait - do longer waits pay bigger spikes?
  4. variance ratio     - is the series a random walk at 2/4/8/16 bars?
                          VR far from 1.0 means trend or mean reversion.
  5. autocorrelation    - lag 1..10 of returns, with 95% bands.
  6. run lengths        - up/down streaks vs what a fair coin produces.

MULTIPLE TESTING
  This runs roughly 6 tests per symbol. At 5% significance you expect
  about one flag per symbol BY CHANCE. Nothing here is a finding on its
  own: a flag is a hypothesis to pre-register and forward test, never a
  reason to trade.

  python3 synth_probe.py                all synthetic feeds
  python3 synth_probe.py Boom           one symbol
"""
import csv, glob, math, os, statistics, sys

HOME = os.path.expanduser("~/goldlab")
RAW = os.path.join(HOME, "data/raw")
SYNTH = ("Volatility_", "Boom_", "Crash_", "Step_", "Jump_")


def load(path):
    b = []
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                b.append((float(r["open"]), float(r["high"]), float(r["low"]),
                          float(r["close"]), float(r.get("spread") or 0)))
            except Exception:
                pass
    return b


def atr_series(b, n=60):
    out, trs = [], []
    for i, x in enumerate(b):
        tr = x[1]-x[2] if i == 0 else max(x[1]-x[2], abs(x[1]-b[i-1][3]),
                                          abs(x[2]-b[i-1][3]))
        trs.append(tr)
        out.append(sum(trs[-n:]) / min(len(trs), n))
    return out


def spikes(b, a, mult=5.0):
    """Directional spike: an outsized bar, tagged by its direction."""
    out = []
    for i in range(len(b)):
        if a[i] <= 0:
            continue
        if (b[i][1] - b[i][2]) > mult * a[i]:
            out.append((i, 1 if b[i][3] > b[i][0] else -1, b[i][3] - b[i][0]))
    return out


def test_arithmetic(b, sp, boot=400):
    """Do the bleed and the jumps net to zero?

    A point estimate is not enough: 5,000 bars is ~3.5 days, and a single
    window can drift by chance. Bootstrap the per-bar move to get a 90%
    interval on the net. If that interval spans zero, the drift is not
    established, whatever the point estimate says.
    """
    import random as _r
    if len(sp) < 5:
        return None
    idx = {i for i, _, _ in sp}
    moves = [b[i][3] - b[i][0] for i in range(len(b))]
    jump = sum(moves[i] for i in idx)
    bleed = sum(moves[i] for i in range(len(b)) if i not in idx)
    net = jump + bleed
    n = len(moves)
    _r.seed(17)
    sims = []
    for _ in range(boot):
        s = sum(moves[_r.randrange(n)] for _ in range(n))
        sims.append(s / n * 1000)
    sims.sort()
    lo, hi = sims[int(0.05 * boot)], sims[int(0.95 * boot) - 1]
    spread = statistics.median([x[4] for x in b if x[4] > 0] or [0])
    return dict(jump=jump, bleed=bleed, net=net, net_per_1k=net / n * 1000,
                ci_lo=lo, ci_hi=hi, spread=spread,
                established=(lo > 0 and hi > 0) or (lo < 0 and hi < 0))


def test_hazard(sp, nbins=5):
    """P(spike at k | not yet) against elapsed time. Flat = memoryless.

    NOTE ON A BUG THAT WAS HERE: the first version bucketed gaps by
    QUANTILE. With equal counts per bucket and a shrinking at-risk pool,
    the rates are forced to 20/25/33/50/100% for ANY distribution - the
    test could never report "flat". Fixed-width bins on the elapsed axis
    are the correct estimator, and the last bin is dropped because every
    surviving gap must terminate there by construction.
    """
    gaps = []
    last = None
    for i, _, _ in sp:
        if last is not None:
            gaps.append(i - last)
        last = i
    if len(gaps) < 30:
        return None
    top = sorted(gaps)[int(len(gaps) * 0.9)]          # ignore the long tail
    w = max(1, top // nbins)
    buckets = []
    for k in range(nbins):
        lo, hi = k * w, (k + 1) * w
        at_risk = sum(1 for g in gaps if g >= lo)
        fired = sum(1 for g in gaps if lo <= g < hi)
        if at_risk >= 15:                              # enough to estimate
            rate = fired / at_risk
            se = math.sqrt(max(rate * (1 - rate), 1e-9) / at_risk)
            buckets.append((lo, hi, rate, at_risk, se))
    m = statistics.mean(gaps)
    sd = statistics.pstdev(gaps)
    return dict(gaps=gaps, buckets=buckets, mean=m, median=statistics.median(gaps),
                sd=sd, cv=sd / m if m else 0)


def test_size_vs_wait(sp):
    """Do longer waits pay bigger spikes? Pearson r on (gap, |size|)."""
    pts = []
    last = None
    for i, _, m in sp:
        if last is not None:
            pts.append((i - last, abs(m)))
        last = i
    if len(pts) < 20:
        return None
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x-mx)*(y-my) for x, y in pts)
    den = math.sqrt(sum((x-mx)**2 for x in xs) * sum((y-my)**2 for y in ys))
    r = num / den if den else 0
    n = len(pts)
    z = r * math.sqrt(n - 1)          # rough significance
    return dict(r=r, n=n, z=z)


def test_variance_ratio(b, qs=(2, 4, 8, 16)):
    """VR(q) ~ 1 for a random walk. >1 trending, <1 mean reverting."""
    px = [x[3] for x in b]
    r1 = [px[i] - px[i-1] for i in range(1, len(px))]
    n = len(r1)
    if n < 500:
        return None
    v1 = statistics.pvariance(r1)
    out = []
    for q in qs:
        rq = [px[i] - px[i-q] for i in range(q, len(px))]
        vq = statistics.pvariance(rq)
        vr = vq / (q * v1) if v1 else float("nan")
        # Lo-MacKinlay homoskedastic z
        z = (vr - 1) / math.sqrt(2.0 * (2*q - 1) * (q - 1) / (3.0 * q * n)) \
            if n else 0
        out.append((q, vr, z))
    return out


def test_autocorr(b, lags=10):
    px = [x[3] for x in b]
    r = [px[i] - px[i-1] for i in range(1, len(px))]
    n = len(r)
    if n < 500:
        return None
    m = statistics.mean(r)
    den = sum((x-m)**2 for x in r)
    out = []
    band = 1.96 / math.sqrt(n)
    for k in range(1, lags+1):
        num = sum((r[i]-m)*(r[i-k]-m) for i in range(k, n))
        out.append((k, num/den if den else 0))
    return dict(ac=out, band=band, n=n)


def test_runs(b):
    px = [x[3] for x in b]
    r = [1 if px[i] > px[i-1] else 0 for i in range(1, len(px))]
    n = len(r)
    if n < 500:
        return None
    runs = 1
    for i in range(1, n):
        if r[i] != r[i-1]:
            runs += 1
    n1 = sum(r); n0 = n - n1
    if n1 == 0 or n0 == 0:
        return None
    exp = 2*n1*n0/n + 1
    var = (2*n1*n0*(2*n1*n0 - n)) / (n*n*(n-1))
    z = (runs - exp) / math.sqrt(var) if var > 0 else 0
    return dict(runs=runs, expected=exp, z=z, up=n1, down=n0, n=n)


def probe(path):
    sym = os.path.basename(path).replace("_live.csv", "")
    b = load(path)
    if len(b) < 1000:
        print(f"  {sym}: {len(b)} bars, need 1000+"); return
    a = atr_series(b)
    sp = spikes(b, a)
    flags = []
    print(f"\n  {sym}   {len(b):,} bars   {len(sp)} spikes")
    print("  " + "=" * 72)

    ar = test_arithmetic(b, sp)
    if ar:
        print(f"  1. BLEED/SPIKE ARITHMETIC")
        print(f"     jumps total {ar['jump']:+,.1f}   bleed total {ar['bleed']:+,.1f}")
        print(f"     net {ar['net']:+,.1f}  ({ar['net_per_1k']:+.2f} per 1000 bars)")
        print(f"     median spread {ar['spread']:.3f}")
        print(f"     90% CI on net per 1000 bars: [{ar['ci_lo']:+.2f}, "
              f"{ar['ci_hi']:+.2f}]")
        if not ar["established"]:
            print(f"     -> interval spans zero: net drift NOT established in "
                  f"this window.")
        elif abs(ar['net_per_1k']) < ar['spread'] * 2:
            print(f"     -> drift is real but smaller than 2x spread. Not "
                  f"harvestable.")
        else:
            print(f"     -> NET DRIFT established and larger than spread. "
                  f"Worth pre-registering a direction test.")
            flags.append("net drift")

    hz = test_hazard(sp)
    if hz:
        print(f"\n  2. HAZARD RATE (memoryless?)  gaps mean {hz['mean']:.0f} "
              f"median {hz['median']:.0f} sd {hz['sd']:.0f}")
        rates = []
        for lo, hi, rate, at_risk, se in hz["buckets"]:
            print(f"     waited {lo:>4}-{hi:<5} bars: fire rate {rate:>6.1%} "
                  f"+/-{1.96*se:.1%}  (at risk {at_risk})")
            rates.append((rate, se))
        print(f"     cv = sd/mean = {hz['cv']:.2f}  (1.00 = exponential = "
              f"memoryless)")
        if len(rates) >= 2:
            (r1, s1), (r2, s2) = rates[0], rates[-1]
            z = (r2 - r1) / math.sqrt(s1*s1 + s2*s2) if (s1 or s2) else 0
            print(f"     first vs last bin: z = {z:+.2f}")
            if abs(z) > 2:
                print(f"     -> hazard is NOT flat: elapsed time changes the odds.")
                flags.append("hazard not flat")
            else:
                print(f"     -> hazard flat within error: waiting tells you "
                      f"nothing.")

    sw = test_size_vs_wait(sp)
    if sw:
        print(f"\n  3. SPIKE SIZE vs WAIT   r = {sw['r']:+.3f}  (n={sw['n']}, "
              f"z={sw['z']:+.2f})")
        if abs(sw["z"]) > 2:
            print(f"     -> longer waits pay {'bigger' if sw['r']>0 else 'smaller'} "
                  f"spikes.")
            flags.append("size~wait")
        else:
            print(f"     -> no relationship. Spike size is independent of the wait.")

    vr = test_variance_ratio(b)
    if vr:
        print(f"\n  4. VARIANCE RATIO (1.00 = random walk)")
        for q, v, z in vr:
            tag = ""
            if abs(z) > 2:
                tag = "  <-- " + ("trending" if v > 1 else "mean reverting")
                flags.append(f"VR q={q}")
            print(f"     q={q:<3} VR={v:.3f}  z={z:+.2f}{tag}")

    ac = test_autocorr(b)
    if ac:
        hits = [(k, v) for k, v in ac["ac"] if abs(v) > ac["band"]]
        print(f"\n  5. AUTOCORRELATION  95% band +/-{ac['band']:.4f}")
        print(f"     significant lags: "
              + (", ".join(f"{k}({v:+.3f})" for k, v in hits) if hits else "none"))
        if hits:
            flags.append(f"autocorr lag {hits[0][0]}")

    ru = test_runs(b)
    if ru:
        print(f"\n  6. RUNS TEST  {ru['runs']} runs vs {ru['expected']:.0f} "
              f"expected   z={ru['z']:+.2f}")
        if abs(ru["z"]) > 2:
            print(f"     -> {'too few' if ru['z']<0 else 'too many'} switches: "
                  f"{'streaky' if ru['z']<0 else 'choppy'} beyond chance.")
            flags.append("runs")
        else:
            print(f"     -> consistent with independent up/down moves.")

    print("\n  " + "-" * 72)
    if flags:
        print(f"  FLAGS: {', '.join(flags)}")
        print("  Roughly 6 tests were run, so about one flag per symbol is")
        print("  EXPECTED by chance. Treat each as a hypothesis to pre-register")
        print("  and forward test - never as a reason to trade.")
    else:
        print("  No deviation from the stated generator. On this evidence the")
        print("  series is untradeable by design, which is a finding, not a")
        print("  failure - it saves the cost of discovering it with money.")


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    paths = [p for p in sorted(glob.glob(os.path.join(RAW, "*_live.csv")))
             if any(m in os.path.basename(p) for m in SYNTH)]
    if only:
        paths = [p for p in paths if only.lower() in os.path.basename(p).lower()]
    if not paths:
        print("  no synthetic feeds found"); return
    for p in paths:
        probe(p)
    print()


if __name__ == "__main__":
    main()
