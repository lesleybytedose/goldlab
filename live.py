#!/usr/bin/env python3
"""
live.py - every model, forward tested in parallel on the broker feed.

Each model logs its signals the moment a bar closes, before the outcome
exists. Resolved trades are scored on later runs. A random control runs
alongside on the same bars, so there is always a benchmark.

  python3 live.py             detect new signals + score open ones
  python3 live.py --report    per-model scoreboard
  python3 live.py --backfill  seed from history (marked separately)
"""
import csv, glob, hashlib, json, os, random, sys
from datetime import datetime, timezone, timedelta

SAST = timezone(timedelta(hours=2))
FEED_TZ = timezone.utc  # EA sends broker time = UTC
HOME = os.path.expanduser("~/goldlab")
RAW = os.path.join(HOME, "data/raw")
LOG = os.path.join(HOME, "logs/live_signals.jsonl")
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


# ---------------------------------------------------------------- models
# Each returns a list of (index, direction, stop_atr, target_atr)

def m1_extreme(b, a, sess):
    """New daily extreme after the first two hours. Momentum continuation."""
    out = []
    for _, idxs in sess.items():
        if len(idxs) < 9:
            continue
        hi = max(b[i][2] for i in idxs[:8])
        lo = min(b[i][3] for i in idxs[:8])
        for i in idxs[8:]:
            c = b[i][4]
            if c > hi:
                out.append((i, "long", 1.0, 2.0)); break
            if c < lo:
                out.append((i, "short", 1.0, 2.0)); break
            hi = max(hi, b[i][2]); lo = min(lo, b[i][3])
    return out


def m1_fade(b, a, sess):
    """The mirror. If M1 has a real edge this must lose."""
    return [(i, "short" if d == "long" else "long", s, t)
            for i, d, s, t in m1_extreme(b, a, sess)]


def m4_crt(b, a, sess):
    """Candle Range Theory: sweep a prior range extreme, close back inside."""
    out = []
    for i in range(30, len(b)):
        if a[i] <= 0:
            continue
        hi = max(x[2] for x in b[i-8:i-1])
        lo = min(x[3] for x in b[i-8:i-1])
        if (hi - lo) < 0.6 * a[i]:
            continue
        h, l, c = b[i][2], b[i][3], b[i][4]
        if h > hi and c < hi:
            out.append((i, "short", 1.0, 2.0))
        elif l < lo and c > lo:
            out.append((i, "long", 1.0, 2.0))
    return out


def m5_orderblock(b, a, sess):
    """
    Order block: the last opposing candle before an impulsive move.
    Trade the first return to that candle's body, in the impulse direction.
    """
    out = []
    for i in range(30, len(b)):
        if a[i] <= 0:
            continue
        # impulse: 3 bars covering 2+ ATR in one direction
        move = b[i][4] - b[i-3][1]
        if abs(move) < 2.0 * a[i]:
            continue
        up = move > 0
        # the block: last candle against the impulse before it started
        blk = None
        for k in range(i-3, max(0, i-10), -1):
            down_candle = b[k][4] < b[k][1]
            if (up and down_candle) or (not up and not down_candle):
                blk = k; break
        if blk is None:
            continue
        top, bot = max(b[blk][1], b[blk][4]), min(b[blk][1], b[blk][4])
        # first retest within the next 20 bars
        for k in range(i + 1, min(i + 21, len(b))):
            if up and b[k][3] <= top and b[k][4] > bot:
                out.append((k, "long", 1.0, 2.0)); break
            if (not up) and b[k][2] >= bot and b[k][4] < top:
                out.append((k, "short", 1.0, 2.0)); break
    return out


def m6_spike(b, a, sess):
    """Range expansion: a bar 2.5x average, trade its direction."""
    out = []
    for i in range(25, len(b)):
        if a[i] <= 0:
            continue
        if (b[i][2] - b[i][3]) < 2.5 * a[i]:
            continue
        out.append((i, "long" if b[i][4] > b[i][1] else "short", 1.0, 1.5))
    return out


def m7_london(b, a, sess):
    """Break of the first hour of the London session."""
    out = []
    for _, idxs in sess.items():
        # London opens 08:00 UTC. Trade the break of its first hour.
        first = [i for i in idxs if 8 <= b[i][0].hour < 9]
        rest  = [i for i in idxs if 9 <= b[i][0].hour < 15]
        if len(first) < 3 or not rest:
            continue
        hi = max(b[i][2] for i in first)
        lo = min(b[i][3] for i in first)
        for i in rest:
            c = b[i][4]
            if c > hi:
                out.append((i, "long", 1.0, 2.0)); break
            if c < lo:
                out.append((i, "short", 1.0, 2.0)); break
    return out


def m8_contraction(b, a, sess):
    """Six quiet bars, then a break either side."""
    out = []
    for i in range(30, len(b)):
        if a[i] <= 0:
            continue
        w = b[i-6:i]
        hi = max(x[2] for x in w); lo = min(x[3] for x in w)
        if (hi - lo) > 1.2 * a[i]:
            continue
        c = b[i][4]
        if c > hi:
            out.append((i, "long", 1.0, 2.0))
        elif c < lo:
            out.append((i, "short", 1.0, 2.0))
    return out


def m0_random(b, a, sess):
    """Control. Deterministic per bar so it never changes between runs."""
    out = []
    for i in range(30, len(b), 6):
        seed = int(hashlib.md5(str(b[i][0]).encode()).hexdigest()[:8], 16)
        out.append((i, "long" if seed % 2 else "short", 1.0, 1.8))
    return out


MODELS = {
    "M1 daily extreme":   m1_extreme,
    "M1 faded":           m1_fade,
    "CRT sweep":          m4_crt,
    "Order block":        m5_orderblock,
    "Range spike":        m6_spike,
    "London break":       m7_london,
    "Contraction break":  m8_contraction,
    "RANDOM CONTROL":     m0_random,
}


# ---------------------------------------------------------------- engine
def read_log():
    rows = []
    if os.path.exists(LOG):
        for ln in open(LOG):
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
    return rows


def write_log(rows):
    tmp = LOG + ".tmp"
    with open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    os.replace(tmp, LOG)


def score(sig, b, idx_by_ts):
    """Walk forward. Returns (R, bars_held), or None if unresolved."""
    i = idx_by_ts.get(sig["ts"])
    if i is None:
        return None
    e, st, tg = sig["entry"], sig["stop"], sig["target"]
    risk = abs(e - st)
    if risk <= 0:
        return None
    rr = abs(tg - e) / risk
    cost = sig.get("spread", 0) / risk
    for k in range(i + 1, min(i + 1 + MAXHOLD, len(b))):
        h, l = b[k][2], b[k][3]
        if sig["dir"] == "long":
            if l <= st: return round(-1.0 - cost, 3), k - i
            if h >= tg: return round(rr - cost, 3), k - i
        else:
            if h >= st: return round(-1.0 - cost, 3), k - i
            if l <= tg: return round(rr - cost, 3), k - i
    if len(b) - i > MAXHOLD + 1:
        return 0.0, MAXHOLD
    return None


def detect(feedname, backfill=False):
    path = os.path.join(RAW, feedname)
    if not os.path.exists(path):
        return 0
    b = load(path)
    if len(b) < 200:
        return 0
    a = atr(b)
    sess = sessions(b)
    sym = feedname.replace("_M15_live.csv", "").replace("_M15.csv", "")

    rows = read_log()
    known = {r["k"] for r in rows if "k" in r}
    idx_by_ts = {x[0].isoformat(): i for i, x in enumerate(b)}
    cutoff = b[-1][0] - timedelta(hours=2)      # only recent bars unless backfilling

    added = 0
    for name, fn in MODELS.items():
        try:
            sigs = fn(b, a, sess)
        except Exception as e:
            print(f"  {name}: error {e}")
            continue
        for i, d, satr, tatr in sigs:
            if not backfill and b[i][0] < cutoff:
                continue
            u = a[i]
            if u <= 0:
                continue
            e = b[i][4]
            k = f"{sym}|{name}|{b[i][0].isoformat()}"
            if k in known:
                continue
            sig = dict(
                k=k, sym=sym, model=name, ts=b[i][0].isoformat(), dir=d,
                entry=round(e, 3),
                stop=round(e - satr*u if d == "long" else e + satr*u, 3),
                target=round(e + tatr*u if d == "long" else e - tatr*u, 3),
                atr=round(u, 3),
                spread=round(b[i][5] if b[i][5] > 0 else u*0.05, 4),
                phase="backfill" if backfill else "forward",
                logged=datetime.now(FEED_TZ).isoformat(timespec="seconds"),
                R=None,
            )
            rows.append(sig)
            known.add(k)
            added += 1

    # score everything open for this symbol
    for r in rows:
        if r.get("sym") == sym and r.get("R") is None:
            v = score(r, b, idx_by_ts)
            if v is not None:
                r["R"], r["held"] = v
    write_log(rows)
    return added


def report():
    rows = read_log()
    for phase in ("forward", "backfill"):
        sub = [r for r in rows if r.get("phase") == phase]
        if not sub:
            continue
        print(f"\n  {'FORWARD TEST' if phase=='forward' else 'BACKFILL (reference only)'}"
              f"   {len(sub)} logged")
        print("  " + "=" * 74)
        print(f"  {'MODEL':22}{'n':>6}{'open':>7}{'win':>8}{'exp':>9}{'total':>10}")
        print("  " + "-" * 74)
        by = {}
        for r in sub:
            by.setdefault(r["model"], []).append(r)
        ctrl = None
        lines = []
        for m in sorted(by):
            R = [x["R"] for x in by[m] if x["R"] is not None]
            op = sum(1 for x in by[m] if x["R"] is None)
            if not R:
                lines.append((m, f"  {m:22}{0:>6}{op:>7}{'—':>8}{'—':>9}{'—':>10}", None))
                continue
            w = [x for x in R if x > 0]
            exp = sum(R) / len(R)
            if m == "RANDOM CONTROL":
                ctrl = exp
            lines.append((m,
                f"  {m:22}{len(R):>6}{op:>7}{len(w)/len(R):>7.1%}"
                f"{exp:>+8.2f}R{sum(R):>+9.1f}R", exp))
        for m, line, exp in lines:
            mark = ""
            if ctrl is not None and exp is not None and m != "RANDOM CONTROL":
                mark = "  beats control" if exp > ctrl + 0.05 else ""
            print(line + mark)
        print("  " + "-" * 74)
    print("""
  The random control is the bar. A model only matters if it stays above it
  once thirty or more of its trades have resolved. Anything below is noise
  wearing a name.
""")


def main():
    if "--report" in sys.argv:
        report(); return
    backfill = "--backfill" in sys.argv
    feeds = [os.path.basename(p) for p in
             sorted(glob.glob(os.path.join(RAW, "*_M15_live.csv")))]
    total = 0
    for f in feeds:
        total += detect(f, backfill)
    stamp = datetime.now(FEED_TZ).strftime("%Y-%m-%d %H:%M")
    print(f"  {stamp}  {total} new signal(s) across {len(feeds)} feed(s)"
          f"{'  [backfill]' if backfill else ''}")


from book_models_live import BOOK_LIVE_MODELS
MODELS.update(BOOK_LIVE_MODELS)

if __name__ == "__main__":
    main()
