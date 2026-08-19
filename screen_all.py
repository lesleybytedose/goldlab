#!/usr/bin/env python3
"""
screen_new.py - six new book models, screened before any of them deploy.

Each is written in live.py's native signature (b, a, sess) -> list of
(index, dir, stop_atr, target_atr), then run over ALL stored history and
compared against the BEST of 12 random-entry variants with matched
geometry on the same bars.

Nothing is registered here. It prints nominations. Only what clears the
random bar gets ported into book_models_live.py - and even then it starts
at Stage 1 with the holdout sealed, like everything else.

  python3 screen_new.py            screen on every feed
  python3 screen_new.py XAUUSDm    one symbol
"""
import csv, glob, hashlib, os, statistics as st, sys
from datetime import datetime, timezone

FEED_TZ = timezone.utc
RAW = os.path.expanduser("~/goldlab/data/raw")
MAXHOLD = 32


def load(path):
    rows = []
    for r in csv.DictReader(open(path)):
        try:
            d = datetime.fromisoformat(f"{r['date']} {r['time']}").replace(tzinfo=FEED_TZ)
            rows.append([d, float(r["open"]), float(r["high"]), float(r["low"]),
                         float(r["close"]), float(r.get("spread") or 0)])
        except Exception:
            pass
    rows.sort(key=lambda b: b[0])
    return rows


def atr(b, n=20):
    out, trs = [], []
    for i, x in enumerate(b):
        tr = x[2]-x[3] if i == 0 else max(x[2]-x[3], abs(x[2]-b[i-1][4]), abs(x[3]-b[i-1][4]))
        trs.append(tr); out.append(sum(trs[-n:])/min(len(trs), n))
    return out


def sessions(b):
    d = {}
    for i, x in enumerate(b):
        d.setdefault(x[0].date(), []).append(i)
    return d


def score(b, a, sigs):
    out = []
    for i, dr, satr, tatr in sigs:
        u = a[i]
        if u <= 0: continue
        e = b[i][4]
        stp = e - satr*u if dr == "long" else e + satr*u
        tg = e + tatr*u if dr == "long" else e - tatr*u
        risk = abs(e-stp)
        if risk <= 0: continue
        rr = abs(tg-e)/risk
        sp = b[i][5] if b[i][5] > 0 else u*0.05
        cost = sp/risk
        R = None
        for k in range(i+1, min(i+1+MAXHOLD, len(b))):
            h, l = b[k][2], b[k][3]
            if dr == "long":
                if l <= stp: R = -1.0-cost; break
                if h >= tg: R = rr-cost; break
            else:
                if h >= stp: R = -1.0-cost; break
                if l <= tg: R = rr-cost; break
        if R is None and len(b)-i > MAXHOLD+1: R = 0.0
        if R is not None: out.append(R)
    return out


# ------------------------------------------------------------ new models
def m_bos_fvg(b, a, sess):
    """M13. Break of structure, then entry on the fair-value gap it left.
    A 3-bar gap: bar i-2 high < bar i low (bullish) means candle i-1 ran
    without trading that range. Enter on the first return into it."""
    out = []
    for i in range(30, len(b)-1):
        u = a[i]
        if u <= 0: continue
        # bullish FVG: gap between high[i-2] and low[i]
        if b[i][3] > b[i-2][2] and (b[i][3]-b[i-2][2]) > 0.25*u:
            top, bot = b[i][3], b[i-2][2]
            if b[i-1][4] <= b[i-1][1]: continue        # need an impulsive up bar
            for k in range(i+1, min(i+16, len(b))):
                if b[k][3] <= top and b[k][4] > bot:
                    out.append((k, "long", 1.0, 2.0)); break
        # bearish FVG
        if b[i][2] < b[i-2][3] and (b[i-2][3]-b[i][2]) > 0.25*u:
            top, bot = b[i-2][3], b[i][2]
            if b[i-1][4] >= b[i-1][1]: continue
            for k in range(i+1, min(i+16, len(b))):
                if b[k][2] >= bot and b[k][4] < top:
                    out.append((k, "short", 1.0, 2.0)); break
    return out


def m_liq_sweep(b, a, sess):
    """M14. Equal highs/lows form a liquidity pool; price sweeps it and
    reverses. Distinct from CRT: needs TWO touches building the level."""
    out = []
    for i in range(40, len(b)):
        u = a[i]
        if u <= 0: continue
        w = b[i-20:i]
        hs = sorted((x[2] for x in w), reverse=True)[:3]
        ls = sorted(x[3] for x in w)[:3]
        if len(hs) == 3 and (hs[0]-hs[2]) < 0.25*u:      # equal highs
            lvl = hs[0]
            if b[i][2] > lvl and b[i][4] < lvl:
                out.append((i, "short", 1.0, 2.0)); continue
        if len(ls) == 3 and (ls[2]-ls[0]) < 0.25*u:      # equal lows
            lvl = ls[0]
            if b[i][3] < lvl and b[i][4] > lvl:
                out.append((i, "long", 1.0, 2.0))
    return out


def m_orb(b, a, sess):
    """M16 (Aziz). Opening range = first 4 bars; trade the first break,
    but only in the first half of the session."""
    out = []
    for _, idxs in sess.items():
        if len(idxs) < 12: continue
        first = idxs[:4]
        hi = max(b[i][2] for i in first)
        lo = min(b[i][3] for i in first)
        for i in idxs[4:len(idxs)//2]:
            c = b[i][4]
            if c > hi: out.append((i, "long", 1.0, 2.0)); break
            if c < lo: out.append((i, "short", 1.0, 2.0)); break
    return out


def m_abcd(b, a, sess):
    """M15 (Aziz). Impulse A->B, retrace B->C of 38-79%, then continuation
    entry when price takes out B."""
    out = []
    for i in range(40, len(b)):
        u = a[i]
        if u <= 0: continue
        w = b[i-30:i]
        lo_i = min(range(len(w)), key=lambda k: w[k][3])
        hi_i = max(range(len(w)), key=lambda k: w[k][2])
        if hi_i > lo_i + 2:                       # up impulse A(lo)->B(hi)
            A, B = w[lo_i][3], w[hi_i][2]
            leg = B - A
            if leg < 1.5*u: continue
            C = min(x[3] for x in w[hi_i:])
            rt = (B - C)/leg if leg else 0
            if 0.38 <= rt <= 0.79 and b[i][4] > B:
                out.append((i, "long", 1.0, 2.0))
        elif lo_i > hi_i + 2:                     # down impulse
            A, B = w[hi_i][2], w[lo_i][3]
            leg = A - B
            if leg < 1.5*u: continue
            C = max(x[2] for x in w[lo_i:])
            rt = (C - B)/leg if leg else 0
            if 0.38 <= rt <= 0.79 and b[i][4] < B:
                out.append((i, "short", 1.0, 2.0))
    return out


def m_keltner(b, a, sess):
    """M34. Pullback to the Keltner mid-line inside a trend."""
    out = []
    for i in range(60, len(b)):
        u = a[i]
        if u <= 0: continue
        n = 20
        m = sum(x[4] for x in b[i-n+1:i+1])/n
        mp = sum(x[4] for x in b[i-n:i])/n
        up, dn = m + 2.25*u, m - 2.25*u
        prev_out_up = any(b[k][4] > up for k in range(i-6, i))
        prev_out_dn = any(b[k][4] < dn for k in range(i-6, i))
        if prev_out_up and m > mp and b[i][3] <= m and b[i][4] > m:
            out.append((i, "long", 1.0, 2.0))
        elif prev_out_dn and m < mp and b[i][2] >= m and b[i][4] < m:
            out.append((i, "short", 1.0, 2.0))
    return out


def m_vol_comp(b, a, sess):
    """M35. Volatility compression: ATR at a 30-bar low, then expansion."""
    out = []
    for i in range(60, len(b)):
        u = a[i]
        if u <= 0: continue
        if a[i-1] > min(a[i-30:i-1]) * 1.05: continue     # was it compressed?
        rng = b[i][2]-b[i][3]
        if rng < 1.6*u: continue                          # now expanding?
        out.append((i, "long" if b[i][4] > b[i][1] else "short", 1.0, 2.0))
    return out


NEW = {"BOS_FVG": m_bos_fvg, "LIQ_SWEEP": m_liq_sweep, "ORB": m_orb,
       "ABCD": m_abcd, "KELTNER": m_keltner, "VOL_COMP": m_vol_comp}



# ------------------------------------------------- remaining candidates
def m_anti(b, a, sess):
    """M22 (Grimes ANTI). Pullback against a short-term trend inside a
    longer-term trend, entered when the short-term turn fails."""
    out = []
    for i in range(60, len(b)):
        u = a[i]
        if u <= 0: continue
        s50 = sum(x[4] for x in b[i-49:i+1])/50
        s10 = sum(x[4] for x in b[i-9:i+1])/10
        s10p = sum(x[4] for x in b[i-10:i])/10
        c = b[i][4]
        if c > s50 and s10 < s10p and b[i][4] > b[i][1] and b[i-1][4] < b[i-1][1]:
            out.append((i, "long", 1.0, 2.0))
        elif c < s50 and s10 > s10p and b[i][4] < b[i][1] and b[i-1][4] > b[i-1][1]:
            out.append((i, "short", 1.0, 2.0))
    return out


def m_candle_confirm(b, a, sess):
    """M26. Engulfing candle at a 20-bar extreme."""
    out = []
    for i in range(40, len(b)):
        u = a[i]
        if u <= 0: continue
        w = b[i-20:i]
        hi = max(x[2] for x in w); lo = min(x[3] for x in w)
        body = abs(b[i][4]-b[i][1]); pbody = abs(b[i-1][4]-b[i-1][1])
        if body < 0.8*u or body < 1.3*pbody: continue
        if b[i][3] <= lo and b[i][4] > b[i][1] and b[i][4] > b[i-1][1]:
            out.append((i, "long", 1.0, 2.0))
        elif b[i][2] >= hi and b[i][4] < b[i][1] and b[i][4] < b[i-1][1]:
            out.append((i, "short", 1.0, 2.0))
    return out


def _stoch(b, i, n=14):
    w = b[i-n+1:i+1]
    hi = max(x[2] for x in w); lo = min(x[3] for x in w)
    return 50.0 if hi == lo else 100.0*(b[i][4]-lo)/(hi-lo)


def m_stoch_bb(b, i_unused=None, sess=None):
    return []


def m_stoch_bb2(b, a, sess):
    """M17. Stochastic turn at a Bollinger band edge."""
    out = []
    for i in range(40, len(b)):
        u = a[i]
        if u <= 0: continue
        n = 20
        w = [x[4] for x in b[i-n+1:i+1]]
        m = sum(w)/n
        sd = (sum((x-m)**2 for x in w)/n) ** 0.5
        if sd <= 0: continue
        k, kp = _stoch(b, i), _stoch(b, i-1)
        if b[i][4] <= m-2*sd and k > kp and kp < 20:
            out.append((i, "long", 1.0, 2.0))
        elif b[i][4] >= m+2*sd and k < kp and kp > 80:
            out.append((i, "short", 1.0, 2.0))
    return out


def m_tf_channel(b, a, sess):
    """M31. Trend-following channel: close beyond the 20-bar channel while
    the 50-bar slope agrees."""
    out = []
    for i in range(60, len(b)):
        u = a[i]
        if u <= 0: continue
        hi = max(x[2] for x in b[i-20:i]); lo = min(x[3] for x in b[i-20:i])
        s50 = sum(x[4] for x in b[i-49:i+1])/50
        s50p = sum(x[4] for x in b[i-50:i])/50
        c = b[i][4]
        if c > hi and s50 > s50p: out.append((i, "long", 1.0, 2.0))
        elif c < lo and s50 < s50p: out.append((i, "short", 1.0, 2.0))
    return out


def m_khoo_tc(b, a, sess):
    """M29. Trend, tight consolidation at the 50-MA, then break."""
    out = []
    for i in range(70, len(b)):
        u = a[i]
        if u <= 0: continue
        m = sum(x[4] for x in b[i-49:i+1])/50
        mp = sum(x[4] for x in b[i-53:i-3])/50
        w = b[i-3:i+1]
        chi = max(x[2] for x in w); clo = min(x[3] for x in w)
        if (chi-clo) > 0.9*u: continue
        if not (clo <= m <= chi or abs(clo-m) < 0.4*u): continue
        if b[i-4][4] > mp and m >= mp and b[i][4] > chi - 0.05*u:
            out.append((i, "long", 1.0, 2.0))
        elif b[i-4][4] < mp and m <= mp and b[i][4] < clo + 0.05*u:
            out.append((i, "short", 1.0, 2.0))
    return out


def m_khoo_bounce(b, a, sess):
    """M30. Bounce off the 50-MA in an established trend."""
    out = []
    for i in range(70, len(b)):
        u = a[i]
        if u <= 0: continue
        m = sum(x[4] for x in b[i-49:i+1])/50
        mp = sum(x[4] for x in b[i-54:i-4])/50
        if m > mp and b[i][3] <= m <= b[i][4] and b[i][4] > b[i][1]:
            out.append((i, "long", 1.0, 2.0))
        elif m < mp and b[i][2] >= m >= b[i][4] and b[i][4] < b[i][1]:
            out.append((i, "short", 1.0, 2.0))
    return out


def m_gmma(b, a, sess):
    """M25. Short EMA ribbon rejoins the long ribbon direction."""
    def ema(vals, n):
        k = 2.0/(n+1); e = vals[0]
        for v in vals[1:]: e = v*k + e*(1-k)
        return e
    out = []
    for i in range(80, len(b)):
        u = a[i]
        if u <= 0: continue
        cl = [x[4] for x in b[:i+1]]
        s = sum(ema(cl[-60:], n) for n in (3, 5, 8, 10, 12, 15))/6
        l = sum(ema(cl[-80:], n) for n in (30, 35, 40, 45, 50, 60))/6
        sp = sum(ema(cl[-61:-1], n) for n in (3, 5, 8, 10, 12, 15))/6
        if s > l and sp <= l: out.append((i, "long", 1.0, 2.0))
        elif s < l and sp >= l: out.append((i, "short", 1.0, 2.0))
    return out


def m_ib_false(b, a, sess):
    """M33. Initial-balance false break: break the first hour then close
    back inside it."""
    out = []
    for _, idxs in sess.items():
        if len(idxs) < 12: continue
        ib = idxs[:4]
        hi = max(b[i][2] for i in ib); lo = min(b[i][3] for i in ib)
        for i in idxs[4:]:
            if b[i][2] > hi and b[i][4] < hi:
                out.append((i, "short", 1.0, 2.0)); break
            if b[i][3] < lo and b[i][4] > lo:
                out.append((i, "long", 1.0, 2.0)); break
    return out


def m_fib_pullback(b, a, sess):
    """M28. Generic mid-plateau pullback (fib framing deliberately dropped:
    entry on a 40-60% retrace of the last swing)."""
    out = []
    for i in range(50, len(b)):
        u = a[i]
        if u <= 0: continue
        w = b[i-30:i]
        lo_i = min(range(len(w)), key=lambda k: w[k][3])
        hi_i = max(range(len(w)), key=lambda k: w[k][2])
        if hi_i > lo_i+2:
            A, B = w[lo_i][3], w[hi_i][2]
            leg = B-A
            if leg < 1.5*u: continue
            lvl = B - 0.5*leg
            if b[i][3] <= lvl <= b[i][2] and b[i][4] > lvl:
                out.append((i, "long", 1.0, 2.0))
        elif lo_i > hi_i+2:
            A, B = w[hi_i][2], w[lo_i][3]
            leg = A-B
            if leg < 1.5*u: continue
            lvl = B + 0.5*leg
            if b[i][3] <= lvl <= b[i][2] and b[i][4] < lvl:
                out.append((i, "short", 1.0, 2.0))
    return out


def m_pdc_open(b, a, sess):
    """M19. Reaction to the previous day's close."""
    out = []
    days = sorted(sess)
    for j in range(1, len(days)):
        prev = sess[days[j-1]]; cur = sess[days[j]]
        pdc = b[prev[-1]][4]
        for i in cur[:12]:
            u = a[i]
            if u <= 0: continue
            if b[i][3] <= pdc <= b[i][2]:
                if b[i][4] > pdc: out.append((i, "long", 1.0, 2.0))
                else: out.append((i, "short", 1.0, 2.0))
                break
    return out


def m_mom_exhaust(b, a, sess):
    """M24. Three shrinking pushes then an opposing close."""
    out = []
    for i in range(40, len(b)):
        u = a[i]
        if u <= 0: continue
        b1 = b[i-3][4]-b[i-3][1]; b2 = b[i-2][4]-b[i-2][1]; b3 = b[i-1][4]-b[i-1][1]
        if b1 > 0 and b2 > 0 and b3 > 0 and b1 > b2 > b3 and b[i][4] < b[i][1] \
           and abs(b[i][4]-b[i][1]) > 0.5*u:
            out.append((i, "short", 1.0, 2.0))
        elif b1 < 0 and b2 < 0 and b3 < 0 and b1 < b2 < b3 and b[i][4] > b[i][1] \
             and abs(b[i][4]-b[i][1]) > 0.5*u:
            out.append((i, "long", 1.0, 2.0))
    return out


def m_donchian_fade(b, a, sess):
    """M32. Fade a 96-bar extreme (mean-reverting instruments only)."""
    out = []
    for i in range(120, len(b)):
        u = a[i]
        if u <= 0: continue
        hi = max(x[2] for x in b[i-96:i]); lo = min(x[3] for x in b[i-96:i])
        if b[i][2] >= hi and b[i][4] < hi: out.append((i, "short", 1.0, 2.0))
        elif b[i][3] <= lo and b[i][4] > lo: out.append((i, "long", 1.0, 2.0))
    return out


NEW.update({
    "ANTI": m_anti, "CANDLE_CONF": m_candle_confirm, "STOCH_BB": m_stoch_bb2,
    "TF_CHANNEL": m_tf_channel, "KHOO_TC": m_khoo_tc, "KHOO_BOUNCE": m_khoo_bounce,
    "GMMA": m_gmma, "IB_FALSE": m_ib_false, "FIB_PULLBACK": m_fib_pullback,
    "PDC_OPEN": m_pdc_open, "MOM_EXHAUST": m_mom_exhaust,
    "DONCH_FADE": m_donchian_fade,
})


def rnd(b, a, sess, step, salt, tatr=2.0):
    out = []
    for i in range(30, len(b), step):
        s = int(hashlib.md5((salt+str(b[i][0])).encode()).hexdigest()[:8], 16)
        out.append((i, "long" if s % 2 else "short", 1.0, tatr))
    return out


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    paths = sorted(glob.glob(os.path.join(RAW, "*_M15_live.csv")))
    if only:
        paths = [p for p in paths if only in os.path.basename(p)]
    tally = {}
    for p in paths:
        b = load(p)
        if len(b) < 1000: continue
        a = atr(b); sess = sessions(b)
        sym = os.path.basename(p).replace("_M15_live.csv", "")
        print("\n" + "="*66)
        print(sym + "   " + str(len(b)) + " bars")
        for name, fn in NEW.items():
            try:
                R = score(b, a, fn(b, a, sess))
            except Exception as e:
                print("   " + name.ljust(12) + " error " + str(e)); continue
            if len(R) < 60:
                print("   " + name.ljust(12) + " only " + str(len(R))
                      + " resolved - skip"); continue
            exp = sum(R)/len(R)
            step = max(2, (len(b)-30)//max(1, len(R)))
            rb = max((lambda x: sum(x)/len(x) if x else -9)(
                     score(b, a, rnd(b, a, sess, step, name+str(j))))
                     for j in range(12))
            ok = exp > rb and exp > 0
            tally.setdefault(name, []).append(1 if ok else 0)
            print("   " + name.ljust(12) + "n=" + str(len(R)).rjust(6)
                  + "   exp " + ("%+.3f" % exp) + "R"
                  + "   best-of-12 random " + ("%+.3f" % rb) + "R"
                  + ("   NOMINATE" if ok else "   luck-level"))
    if tally:
        print("\n" + "="*66)
        print("ACROSS SYMBOLS - nominations per model (consistency matters more")
        print("than any single symbol; one hit in nine is noise):")
        for k in sorted(tally, key=lambda x: -sum(tally[x])):
            print("   " + k.ljust(12) + str(sum(tally[k])) + " / "
                  + str(len(tally[k])) + " symbols")


main()
