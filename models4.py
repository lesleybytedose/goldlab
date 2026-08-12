#!/usr/bin/env python3
"""
models4.py - the same models, but scale invariant.

Everything is expressed in ATR units, so a rule means the same thing on
gold at 4400 and on EURUSD at 1.15. Spread is charged as a fraction of ATR,
which is how cost actually bites.

  python3 models4.py
  python3 models4.py --file EURUSDc_M15_live.csv
  python3 models4.py --all          every *_live.csv and exness file found
"""
import csv, glob, os, random, sys
from datetime import datetime, timezone, timedelta

random.seed(307)
SAST = timezone(timedelta(hours=2))
RAW = os.path.expanduser("~/goldlab/data/raw")
MAXHOLD = 32
MIN_TRADES = 40


def load(name):
    rows = []
    path = name if os.path.isabs(name) else os.path.join(RAW, name)
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                d = datetime.fromisoformat(f"{r['date']} {r['time']}").replace(tzinfo=SAST)
                rows.append([d, float(r["open"]), float(r["high"]),
                             float(r["low"]), float(r["close"]),
                             float(r.get("spread") or 0)])
            except Exception:
                pass
    rows.sort(key=lambda b: b[0])
    return rows


def atr(b, n=20):
    out, trs = [], []
    for i, x in enumerate(b):
        tr = x[2] - x[3] if i == 0 else max(x[2] - x[3],
                                            abs(x[2] - b[i-1][4]),
                                            abs(x[3] - b[i-1][4]))
        trs.append(tr)
        out.append(sum(trs[-n:]) / min(len(trs), n))
    return out


def sim(b, a, i, d, e, stop_atr, tgt_atr):
    """Stop and target given in ATR units. Returns R after real spread."""
    unit = a[i]
    if unit <= 0:
        return None
    risk = stop_atr * unit
    if risk <= 0:
        return None
    stop = e - risk if d == "long" else e + risk
    tgt = e + tgt_atr * unit if d == "long" else e - tgt_atr * unit
    rr = tgt_atr / stop_atr
    sp = b[i][5] if b[i][5] > 0 else unit * 0.05
    cost = sp / risk
    if cost > 0.5:            # spread eats half the risk: untradeable
        return None
    for k in range(i + 1, min(i + 1 + MAXHOLD, len(b))):
        h, l = b[k][2], b[k][3]
        if d == "long":
            if l <= stop: return -1.0 - cost
            if h >= tgt: return rr - cost
        else:
            if h >= stop: return -1.0 - cost
            if l <= tgt: return rr - cost
    return None


# ---------------- models: all thresholds in ATR units ----------------

def m_dayext(b, a):
    """New daily extreme after the first two hours. Momentum."""
    days, out = {}, []
    for x in b: days.setdefault(x[0].date(), []).append(x)
    idx = {id(x): i for i, x in enumerate(b)}
    for d, rows in days.items():
        if len(rows) < 20: continue
        hi = max(x[2] for x in rows[:8]); lo = min(x[3] for x in rows[:8])
        for x in rows[8:]:
            i = idx[id(x)]; c = x[4]
            if c > hi: out.append((i, "long", c, 1.0, 2.0)); break
            if c < lo: out.append((i, "short", c, 1.0, 2.0)); break
            hi = max(hi, x[2]); lo = min(lo, x[3])
    return out


def m_dayfade(b, a):
    """Same trigger, faded. The control for the above."""
    return [(i, "short" if d == "long" else "long", e, s, t)
            for i, d, e, s, t in m_dayext(b, a)]


def m_spike_follow(b, a):
    out = []
    for i in range(25, len(b) - 1):
        if a[i] <= 0: continue
        rng = (b[i][2] - b[i][3]) / a[i]
        if rng < 2.5: continue
        c = b[i][4]
        out.append((i, "long" if c > b[i][1] else "short", c, 1.0, 1.5))
    return out


def m_spike_fade(b, a):
    return [(i, "short" if d == "long" else "long", e, s, t)
            for i, d, e, s, t in m_spike_follow(b, a)]


def m_nr(b, a):
    """Contraction then expansion."""
    out = []
    for i in range(30, len(b) - 1):
        if a[i] <= 0: continue
        w = b[i-6:i]
        rng = (max(x[2] for x in w) - min(x[3] for x in w)) / a[i]
        if rng > 1.2: continue
        hi = max(x[2] for x in w); lo = min(x[3] for x in w)
        c = b[i][4]
        if c > hi: out.append((i, "long", c, 1.0, 2.0))
        elif c < lo: out.append((i, "short", c, 1.0, 2.0))
    return out


def m_trend(b, a):
    """Continuation: above the 50-bar mean and making progress."""
    out = []
    for i in range(60, len(b) - 1, 4):
        if a[i] <= 0: continue
        m = sum(x[4] for x in b[i-50:i]) / 50
        c = b[i][4]
        z = (c - m) / a[i]
        if z > 1.5: out.append((i, "long", c, 1.5, 2.5))
        elif z < -1.5: out.append((i, "short", c, 1.5, 2.5))
    return out


def m_revert(b, a):
    """Reversion: the mirror of the above."""
    return [(i, "short" if d == "long" else "long", e, s, t)
            for i, d, e, s, t in m_trend(b, a)]


def m_wick(b, a):
    out = []
    for i in range(25, len(b) - 1):
        hi, lo, op, cl = b[i][2], b[i][3], b[i][1], b[i][4]
        rng = hi - lo
        if a[i] <= 0 or rng < a[i]: continue
        body = abs(cl - op)
        up = hi - max(op, cl); dn = min(op, cl) - lo
        if dn > rng * 0.6 and body < rng * 0.3:
            out.append((i, "long", cl, 1.0, 2.0))
        elif up > rng * 0.6 and body < rng * 0.3:
            out.append((i, "short", cl, 1.0, 2.0))
    return out


def m_openbreak(b, a):
    """Break of the first hour of the London session."""
    days, out = {}, []
    for x in b: days.setdefault(x[0].date(), []).append(x)
    idx = {id(x): i for i, x in enumerate(b)}
    for d, rows in days.items():
        first = [x for x in rows if 9 <= x[0].hour < 10]
        rest = [x for x in rows if 10 <= x[0].hour < 16]
        if len(first) < 3 or not rest: continue
        hi = max(x[2] for x in first); lo = min(x[3] for x in first)
        for x in rest:
            i = idx[id(x)]; c = x[4]
            if c > hi: out.append((i, "long", c, 1.0, 2.0)); break
            if c < lo: out.append((i, "short", c, 1.0, 2.0)); break
    return out


def m_random(b, a):
    out = []
    for i in range(30, len(b) - 1, 30):
        c = b[i][4]
        out.append((i, random.choice(["long", "short"]), c, 1.0, 1.8))
    return out


MODELS = [
    ("New daily extreme", m_dayext),
    ("New daily extreme FADED", m_dayfade),
    ("Range spike follow", m_spike_follow),
    ("Range spike fade", m_spike_fade),
    ("Contraction break", m_nr),
    ("Trend continuation", m_trend),
    ("Mean reversion", m_revert),
    ("Rejection wick", m_wick),
    ("London range break", m_openbreak),
    ("RANDOM CONTROL", m_random),
]


def stats(R):
    w = [x for x in R if x > 0]; l = [x for x in R if x <= 0]
    exp = sum(R) / len(R)
    pf = sum(w) / abs(sum(l)) if l else 99.0
    h = len(R) // 2
    e1 = sum(R[:h]) / h; e2 = sum(R[h:]) / (len(R) - h)
    p = sum(1 for _ in range(1500)
            if sum(random.choice(R) * random.choice([1, -1])
                   for _ in range(len(R))) / len(R) >= exp) / 1500
    return len(R), len(w) / len(R), exp, pf, e1, e2, p


def run_file(fn):
    b = load(fn)
    if len(b) < 500:
        print(f"  {fn}: only {len(b)} bars"); return {}
    a = atr(b)
    print(f"\n  {len(b):,} bars  {b[0][0].date()} to {b[-1][0].date()}  ({fn})")
    print("=" * 96)
    print(f"  {'MODEL':26}{'n':>6}{'win':>8}{'exp':>9}{'pf':>7}{'halves':>16}{'p':>8}")
    print("=" * 96)
    res = {}
    for name, fn2 in MODELS:
        try: sigs = fn2(b, a)
        except Exception as e:
            print(f"  {name:26} error: {e}"); continue
        R = [r for r in (sim(b, a, i, d, e, s, t) for i, d, e, s, t in sigs)
             if r is not None]
        if len(R) < MIN_TRADES:
            print(f"  {name:26}{len(R):>6}   too few"); continue
        n, win, exp, pf, e1, e2, p = stats(R)
        star = " ***" if exp > 0.05 and p < 0.05 and e1 > 0 and e2 > 0 else ""
        res[name] = exp
        print(f"  {name:26}{n:>6}{win:>7.1%}{exp:>+8.2f}R{pf:>7.2f}"
              f"{e1:>+8.2f}/{e2:>+.2f}{p:>8.3f}{star}")
    print("=" * 96)
    return res


def main():
    if "--all" in sys.argv:
        files = sorted(set(
            [os.path.basename(x) for x in glob.glob(os.path.join(RAW, "*_live.csv"))] +
            [os.path.basename(x) for x in glob.glob(os.path.join(RAW, "*exness*.csv"))]))
        allres = {}
        for f in files:
            allres[f] = run_file(f)
        print("\n" + "=" * 96)
        print("  ACROSS EVERYTHING — a model has to work in more than one place")
        print("=" * 96)
        names = set()
        for r in allres.values(): names |= set(r)
        print(f"  {'MODEL':26}" + "".join(f"{os.path.basename(f)[:12]:>14}" for f in files))
        print("-" * 96)
        for nm in sorted(names):
            row = "".join(f"{allres[f].get(nm, float('nan')):>+13.2f}R"
                          if nm in allres[f] else f"{'  -':>14}" for f in files)
            print(f"  {nm:26}{row}")
        print("-" * 96)
        print("\n  A model that is positive on one instrument and negative on")
        print("  another has not found anything. Look for consistency, not size.\n")
    else:
        fn = "XAUUSD_M15_exness.csv"
        if "--file" in sys.argv: fn = sys.argv[sys.argv.index("--file") + 1]
        run_file(fn)
        print()


if __name__ == "__main__":
    main()
