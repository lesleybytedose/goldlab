#!/usr/bin/env python3
"""
sweep.py - pre-registered parameter sweeps on backfill history, judged
against an equal-sized sweep of random variants.

The rule (from the spec): a parameter grid of G variants is only allowed
to nominate a winner if that winner beats the BEST of G random variants
run on the same bars with the same stop/target geometry and a similar
trade count. Otherwise the whole family is "indistinguishable from luck"
and nominates nothing.

At most ONE variant per family may be promoted to a forward slot, and
only by hand, after reading this report.

  python3 sweep.py                 run all family sweeps on all feeds
  python3 sweep.py XAUUSDm         one symbol only
"""
import csv, glob, hashlib, os, statistics, sys
from datetime import datetime, timezone

FEED_TZ = timezone.utc
HOME = os.path.expanduser("~/goldlab")
RAW = os.path.join(HOME, "data/raw")
MAXHOLD = 32

# ---------------------------------------------------------------- data
def load(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                d = datetime.fromisoformat(f"{r['date']} {r['time']}").replace(tzinfo=FEED_TZ)
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
        tr = x[2]-x[3] if i == 0 else max(x[2]-x[3], abs(x[2]-b[i-1][4]), abs(x[3]-b[i-1][4]))
        trs.append(tr)
        out.append(sum(trs[-n:]) / min(len(trs), n))
    return out

def sessions(b):
    d = {}
    for i, x in enumerate(b):
        d.setdefault(x[0].date(), []).append(i)
    return d

def run(b, a, sigs):
    """Score a list of (i, dir, satr, tatr) exactly like live.py does."""
    out = []
    for i, d, satr, tatr in sigs:
        u = a[i]
        if u <= 0:
            continue
        e = b[i][4]
        st = e - satr*u if d == "long" else e + satr*u
        tg = e + tatr*u if d == "long" else e - tatr*u
        risk = abs(e - st)
        if risk <= 0:
            continue
        rr = abs(tg - e) / risk
        sp = b[i][5] if b[i][5] > 0 else u * 0.05
        cost = sp / risk
        R = None
        for k in range(i + 1, min(i + 1 + MAXHOLD, len(b))):
            h, l = b[k][2], b[k][3]
            if d == "long":
                if l <= st: R = -1.0 - cost; break
                if h >= tg: R = rr - cost; break
            else:
                if h >= st: R = -1.0 - cost; break
                if l <= tg: R = rr - cost; break
        if R is None and len(b) - i > MAXHOLD + 1:
            R = 0.0
        if R is not None:
            out.append(R)
    return out

# --------------------------------------------------- parameterised models
def crt(b, a, sess, look, minr):
    out = []
    for i in range(30, len(b)):
        if a[i] <= 0: continue
        hi = max(x[2] for x in b[i-look:i-1]); lo = min(x[3] for x in b[i-look:i-1])
        if (hi - lo) < minr * a[i]: continue
        h, l, c = b[i][2], b[i][3], b[i][4]
        if h > hi and c < hi: out.append((i, "short", 1.0, 2.0))
        elif l < lo and c > lo: out.append((i, "long", 1.0, 2.0))
    return out

def contraction(b, a, sess, bars, tight):
    out = []
    for i in range(30, len(b)):
        if a[i] <= 0: continue
        w = b[i-bars:i]
        hi = max(x[2] for x in w); lo = min(x[3] for x in w)
        if (hi - lo) > tight * a[i]: continue
        c = b[i][4]
        if c > hi: out.append((i, "long", 1.0, 2.0))
        elif c < lo: out.append((i, "short", 1.0, 2.0))
    return out

def donchian(b, a, sess, p):
    out, last = [], None
    for i in range(p + 2, len(b)):
        if a[i] <= 0: continue
        hi = max(x[2] for x in b[i-p:i]); lo = min(x[3] for x in b[i-p:i])
        c = b[i][4]
        if c > hi and last != "long": out.append((i, "long", 1.0, 2.0)); last = "long"
        elif c < lo and last != "short": out.append((i, "short", 1.0, 2.0)); last = "short"
    return out

def ma_band(b, a, sess, n, width):
    out = []
    csum, q = 0.0, []
    for i in range(len(b)):
        q.append(b[i][4]); csum += b[i][4]
        if len(q) > n: csum -= q.pop(0)
        if len(q) < n or a[i] <= 0 or i < n + 1: continue
        m = csum / n
        band = width * a[i]
        c, cp = b[i][4], b[i-1][4]
        if c > m + band and cp <= m + band: out.append((i, "long", 1.0, 2.0))
        elif c < m - band and cp >= m - band: out.append((i, "short", 1.0, 2.0))
    return out

def orderblock(b, a, sess, impulse):
    out = []
    for i in range(30, len(b)):
        if a[i] <= 0: continue
        move = b[i][4] - b[i-3][1]
        if abs(move) < impulse * a[i]: continue
        up = move > 0
        blk = None
        for k in range(i-3, max(0, i-10), -1):
            dn = b[k][4] < b[k][1]
            if (up and dn) or (not up and not dn): blk = k; break
        if blk is None: continue
        top, bot = max(b[blk][1], b[blk][4]), min(b[blk][1], b[blk][4])
        for k in range(i + 1, min(i + 21, len(b))):
            if up and b[k][3] <= top and b[k][4] > bot:
                out.append((k, "long", 1.0, 2.0)); break
            if (not up) and b[k][2] >= bot and b[k][4] < top:
                out.append((k, "short", 1.0, 2.0)); break
    return out

def spike(b, a, sess, mult):
    out = []
    for i in range(25, len(b)):
        if a[i] <= 0: continue
        if (b[i][2] - b[i][3]) < mult * a[i]: continue
        out.append((i, "long" if b[i][4] > b[i][1] else "short", 1.0, 1.5))
    return out

def rnd(b, a, sess, step, salt, tatr):
    out = []
    for i in range(30, len(b), step):
        seed = int(hashlib.md5((salt + str(b[i][0])).encode()).hexdigest()[:8], 16)
        out.append((i, "long" if seed % 2 else "short", 1.0, tatr))
    return out

# Pre-registered grids. These are the grids from the spec — do not extend
# them after seeing results. That is the whole point of pre-registration.
FAMILIES = {
  "CRT sweep":         (2.0, [("look",lk,"minr",mr) for lk in (6,8,10,12) for mr in (0.4,0.6,0.8)],
                        lambda b,a,s,lk,mr: crt(b,a,s,lk,mr)),
  "Contraction break": (2.0, [("bars",n,"tight",t) for n in (4,6,8) for t in (0.8,1.2,1.6)],
                        lambda b,a,s,n,t: contraction(b,a,s,n,t)),
  "Donchian SAR":      (2.0, [("p",p) for p in (7,10,14,20,28)],
                        lambda b,a,s,p: donchian(b,a,s,p)),
  "MA/ATR band":       (2.0, [("n",n,"w",w) for n in (20,35,50) for w in (0.4,0.6,0.9)],
                        lambda b,a,s,n,w: ma_band(b,a,s,n,w)),
  "Order block":       (2.0, [("impulse",x) for x in (1.5,2.0,2.5,3.0)],
                        lambda b,a,s,x: orderblock(b,a,s,x)),
  "Range spike":       (1.5, [("mult",x) for x in (2.0,2.5,3.0,3.5)],
                        lambda b,a,s,x: spike(b,a,s,x)),
}

def sweep_symbol(path):
    b = load(path)
    if len(b) < 800:
        return None
    a = atr(b); sess = sessions(b)
    sym = os.path.basename(path).replace("_M15_live.csv","").replace("_M15.csv","")
    print(f"\n  {sym}  ({len(b)} bars)")
    print("  " + "=" * 76)
    results = {}
    for fam, (tatr, grid, fn) in FAMILIES.items():
        G = len(grid)
        rows = []
        for g in grid:
            params = dict(zip(g[::2], g[1::2]))
            R = run(b, a, fn(b, a, sess, *g[1::2]))
            if len(R) < 60:
                continue
            rows.append((sum(R)/len(R), len(R), params))
        if not rows:
            print(f"  {fam:20s} no variant reached 60 resolved — skip")
            continue
        rows.sort(key=lambda r: r[0], reverse=True)
        best_exp, best_n, best_p = rows[0]
        # the null: G random variants, step chosen to land near the median n
        med_n = sorted(r[1] for r in rows)[len(rows)//2]
        step = max(2, (len(b) - 30) // max(1, med_n))
        rand_best = max(
            (lambda R: sum(R)/len(R) if R else -9)(run(b, a, rnd(b, a, sess, step, f"{fam}|{j}|", tatr)))
            for j in range(max(G, 10)))
        verdict = ("NOMINATE" if best_exp > rand_best and best_exp > 0
                   else "luck-level")
        results[fam] = (verdict, best_p, best_exp, best_n, rand_best)
        print(f"  {fam:20s} best {best_p}  exp {best_exp:+.3f}R n={best_n}"
              f"   best-of-{max(G,10)} random {rand_best:+.3f}R   -> {verdict}")
    print("  " + "-" * 76)
    print("  NOMINATE means: this variant beat the best of an equal number of")
    print("  coin-flip variants IN SAMPLE. It earns a forward slot, nothing more.")
    return results

def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    paths = sorted(glob.glob(os.path.join(RAW, "*_M15_live.csv")))
    if only:
        paths = [p for p in paths if only in os.path.basename(p)]
    if not paths:
        print("  no feeds found"); return
    for p in paths:
        try:
            sweep_symbol(p)
        except Exception as e:
            print(f"  {os.path.basename(p)}: error {e}")

if __name__ == "__main__":
    main()
