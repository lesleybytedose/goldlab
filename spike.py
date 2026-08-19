#!/usr/bin/env python3
"""
spike.py - the Boom/Crash tick-count hypothesis, pre-registered.

THE CLAIM BEING TESTED
  Boom/Crash spikes carry no chart signature beforehand; the only variable
  that can carry information is how long it has been since the last spike.
  So: enter in the spike direction once bars-since-last-spike exceeds a
  threshold, and hold long enough for a spike to actually arrive.

PRE-REGISTERED 2026-08-19, BEFORE ANY RESULTS WERE SEEN
  spike bar   : range > SPIKE_ATR x ATR(60) on M1
  entry       : Boom -> long, Crash -> short, when bars_since_spike >= T
  T grid      : 400, 600, 800, 1000
  stop        : STOP_ATR x ATR(60), fixed, NOT tuned to fit
  exit arms   : (a) fixed 2R
                (b) hold until the next spike, or MAXBARS, whichever first
                (c) trail at TRAIL_ATR x ATR after entry
  control     : same arms, entered at RANDOM bar counts, matched frequency
  verdict     : a variant may only be nominated if it beats the BEST of an
                equal number of random-count variants on the same data

WHY THE EXIT ARMS MATTER MORE THAN THE ENTRY
  The whole payoff of Boom lives in one bar. A fixed 2R target truncates
  the spike while keeping every bleed loss - the worst of both. Arm (b)
  exists to let the tail pay. If arm (a) wins, that is itself evidence the
  spike is not what makes the money.

  python3 spike.py                    both symbols, all arms
  python3 spike.py Boom_1000_Index
"""
import csv, glob, hashlib, os, statistics, sys

HOME = os.path.expanduser("~/goldlab")
RAW = os.path.join(HOME, "data/raw")

SPIKE_ATR = 5.0        # a spike bar is this many ATRs of range
STOP_ATR  = 3.0        # fixed stop, pre-registered
TRAIL_ATR = 2.0
MAXBARS   = 1500       # arm (b) gives up after this many bars
T_GRID    = [400, 600, 800, 1000]


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


def spike_flags(b, a):
    """True where the bar's range exceeds SPIKE_ATR x ATR."""
    return [(b[i][1] - b[i][2]) > SPIKE_ATR * a[i] if a[i] > 0 else False
            for i in range(len(b))]


def since_spike(flags):
    out, c = [], 10**6
    for f in flags:
        out.append(c)
        c = 0 if f else c + 1
    return out


def run_trade(b, a, i, direction, arm, flags):
    """Score one entry. Returns R or None if never resolved."""
    if a[i] <= 0:
        return None
    e = b[i][3]
    risk = STOP_ATR * a[i]
    if risk <= 0:
        return None
    cost = (b[i][4] if b[i][4] > 0 else a[i] * 0.05) / risk
    sgn = 1 if direction == "long" else -1
    stop = e - sgn * risk
    best = e
    for k in range(i + 1, min(i + MAXBARS, len(b))):
        h, l = b[k][1], b[k][2]
        # stop first, always: the pessimistic assumption
        if (sgn == 1 and l <= stop) or (sgn == -1 and h >= stop):
            return round(-1.0 - cost, 3)
        if arm == "fixed2R":
            tg = e + sgn * 2 * risk
            if (sgn == 1 and h >= tg) or (sgn == -1 and l <= tg):
                return round(2.0 - cost, 3)
        elif arm == "till_spike":
            if flags[k]:
                px = b[k][3]
                return round(sgn * (px - e) / risk - cost, 3)
        elif arm == "trail":
            best = max(best, h) if sgn == 1 else min(best, l)
            trail = best - sgn * TRAIL_ATR * a[k]
            if (sgn == 1 and l <= trail) or (sgn == -1 and h >= trail):
                return round(sgn * (trail - e) / risk - cost, 3)
    return None


def variant(b, a, flags, ss, T, direction, arm, rnd_salt=None):
    """Real: enter when bars-since-spike >= T. Control: random entries at
    the same rate."""
    Rs, last = [], -10**9
    if rnd_salt is None:
        for i in range(60, len(b) - 5):
            if ss[i] >= T and i - last > T // 2:
                r = run_trade(b, a, i, direction, arm, flags)
                if r is not None:
                    Rs.append(r); last = i
    else:
        # match the real variant's trade count by using the same spacing
        for i in range(60, len(b) - 5):
            if i - last <= T // 2:
                continue
            h = int(hashlib.md5(f"{rnd_salt}|{i}".encode()).hexdigest()[:8], 16)
            if h % max(1, T) == 0:
                r = run_trade(b, a, i, direction, arm, flags)
                if r is not None:
                    Rs.append(r); last = i
    return Rs


def analyse(path):
    sym = os.path.basename(path).replace("_M1_live.csv", "")
    b = load(path)
    if len(b) < 3000:
        print(f"  {sym}: only {len(b)} M1 bars - need 3000+ before this means "
              f"anything. Let the feed run.")
        return
    a = atr_series(b)
    flags = spike_flags(b, a)
    ss = since_spike(flags)
    n_sp = sum(flags)
    gaps = []
    c = 0
    for f in flags:
        c += 1
        if f:
            gaps.append(c); c = 0
    direction = "long" if "Boom" in sym else "short"
    print(f"\n  {sym}   {len(b):,} M1 bars   {n_sp} spikes detected")
    if gaps:
        print(f"  bars between spikes: median {statistics.median(gaps):.0f}  "
              f"min {min(gaps)}  max {max(gaps)}")
    print(f"  entry direction: {direction}  (by instrument design)")
    print("  " + "=" * 70)
    print(f"  {'arm':<12}{'T':>6}{'n':>6}{'mean R':>10}{'median':>9}"
          f"{'best rnd':>10}  verdict")
    print("  " + "-" * 70)
    for arm in ("fixed2R", "till_spike", "trail"):
        for T in T_GRID:
            Rs = variant(b, a, flags, ss, T, direction, arm)
            if len(Rs) < 20:
                print(f"  {arm:<12}{T:>6}{len(Rs):>6}   too few trades")
                continue
            m = sum(Rs) / len(Rs)
            med = statistics.median(Rs)
            rnd = max((lambda v: sum(v)/len(v) if len(v) >= 10 else -9)(
                        variant(b, a, flags, ss, T, direction, arm, rnd_salt=f"{arm}{T}{j}"))
                      for j in range(10))
            verdict = "NOMINATE" if (m > rnd and m > 0) else "luck-level"
            print(f"  {arm:<12}{T:>6}{len(Rs):>6}{m:>+10.3f}{med:>+9.2f}"
                  f"{rnd:>+10.3f}  {verdict}")
    print("  " + "-" * 70)
    print("  Mean vs median matters here: a positive mean with a negative")
    print("  median is a lottery, not an edge. Both are shown deliberately.")


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    paths = sorted(glob.glob(os.path.join(RAW, "*_M1_live.csv")))
    paths = [p for p in paths if "Boom" in p or "Crash" in p]
    if only:
        paths = [p for p in paths if only.lower() in os.path.basename(p).lower()]
    if not paths:
        print("  no Boom/Crash M1 feed yet - attach GoldlabFeed v1.20 with")
        print("  InpTF = PERIOD_M1 to a Boom 1000 and Crash 1000 chart.")
        return
    for p in paths:
        analyse(p)


if __name__ == "__main__":
    main()
