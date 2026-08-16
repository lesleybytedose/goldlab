#!/usr/bin/env python3
"""
book_models_live.py - book-derived models in live.py's native signature.

Each function takes (b, a, sess) exactly like the models in live.py:
  b    list of [datetime, open, high, low, close, spread]
  a    precomputed ATR list (same length as b)
  sess dict {date: [bar indices]}
and returns a list of (index, direction, stop_atr, target_atr).

Structural stops are converted to ATR multiples at signal time, so the
existing engine (entry at close, ATR-multiple stop/target, spread cost,
MAXHOLD scoring) needs no changes.

Also included: geometry-matched random controls (one per stop/target
geometry in use) and a random-level control for the level-fed model,
per the evaluation methodology in goldlab_book_models_spec.md.

Wire-up in live.py (two lines):
    from book_models_live import BOOK_LIVE_MODELS
    MODELS.update(BOOK_LIVE_MODELS)
"""
import hashlib

# --------------------------------------------------------------- helpers

def _sma(b, i, n):
    if i + 1 < n:
        return None
    return sum(x[4] for x in b[i - n + 1:i + 1]) / n


def _clamp_atr(dist, u, lo=0.4, hi=3.0):
    """Convert a structural price distance to an ATR multiple, clamped so a
    degenerate structure can't create a micro-stop (median-stop<1ATR flag in
    the spec) or an unpayable one."""
    if u <= 0:
        return None
    m = dist / u
    if m < lo:
        m = lo
    if m > hi:
        return None          # structure too far away: skip, don't distort
    return round(m, 2)


def _prev_session_levels(b, sess):
    """{date: (prev_high, prev_low, prev_close)} from the prior session."""
    days = sorted(sess)
    out = {}
    for j in range(1, len(days)):
        idxs = sess[days[j - 1]]
        out[days[j]] = (max(b[i][2] for i in idxs),
                        min(b[i][3] for i in idxs),
                        b[idxs[-1]][4])
    return out


def _floor_pivots(ph, pl, pc):
    p = (ph + pl + pc) / 3.0
    rng = ph - pl
    return {"P": p, "R1": 2*p - pl, "S1": 2*p - ph,
            "R2": p + rng, "S2": p - rng}


# --------------------------------------------------------------- models

def bk_ma_atr_band(b, a, sess, n=50, k=3):
    """M11 (Bansal ch.11): close beyond SMA(n) +/- k-period-ish ATR band.
    His grids: MA 20-50, ATR 3-7 on hourly; we run the pre-registered
    center (50, 3) on M15 and leave the grid to the offline sweep."""
    out = []
    for i in range(n + 5, len(b)):
        u = a[i]
        m = _sma(b, i, n)
        if u <= 0 or m is None:
            continue
        band = 0.6 * k * u / 3.0          # scale: k in "ATR units of band width"
        up, dn = m + band, m - band
        c, cp = b[i][4], b[i - 1][4]
        mp = _sma(b, i - 1, n)
        if mp is None:
            continue
        bandp = band
        if c > up and cp <= mp + bandp:
            out.append((i, "long", 1.0, 2.0))
        elif c < dn and cp >= mp - bandp:
            out.append((i, "short", 1.0, 2.0))
    return out


def bk_donchian_sar(b, a, sess, p=20):
    """M12 (Bansal HHV/LLV): close beyond the p-bar channel. Signal only on
    the crossing bar (stop-and-reverse handled naturally by opposite
    signals later). Grid 7-28 pre-registered for the offline sweep."""
    out = []
    last = None
    for i in range(p + 2, len(b)):
        u = a[i]
        if u <= 0:
            continue
        hi = max(x[2] for x in b[i - p:i])
        lo = min(x[3] for x in b[i - p:i])
        c = b[i][4]
        if c > hi and last != "long":
            out.append((i, "long", 1.0, 2.0)); last = "long"
        elif c < lo and last != "short":
            out.append((i, "short", 1.0, 2.0)); last = "short"
    return out


def bk_failure_test(b, a, sess):
    """M21 (Grimes; independently Bansal trendline strategy 3): poke beyond
    the PRIOR session's high/low, close back inside -> fade. Stop is
    structural: beyond the sweep extreme."""
    out = []
    lv = _prev_session_levels(b, sess)
    for day, idxs in sess.items():
        if day not in lv:
            continue
        ph, pl, _ = lv[day]
        for i in idxs:
            u = a[i]
            if u <= 0:
                continue
            h, l, c = b[i][2], b[i][3], b[i][4]
            if h > ph and c < ph:
                s = _clamp_atr(h - c, u)
                if s:
                    out.append((i, "short", s, 2.0 * s)); break
            if l < pl and c > pl:
                s = _clamp_atr(c - l, u)
                if s:
                    out.append((i, "long", s, 2.0 * s)); break
    return out


def bk_break_retest(b, a, sess):
    """M23 universal template, level source = prior-day floor pivots
    (R1/S1). Break the level on close, then first retest that holds ->
    enter with the trend. Stop structural: beyond the retest extreme."""
    return _break_retest_core(b, a, sess, jitter=None)


def bk_break_retest_rndlvl(b, a, sess):
    """RANDOM-LEVEL CONTROL for bk_break_retest (Grimes): identical logic,
    but the level is the pivot displaced by a deterministic pseudo-random
    offset of +/-0.5-1.5 daily ranges. If the real levels matter, this
    must underperform bk_break_retest."""
    return _break_retest_core(b, a, sess, jitter=True)


def _break_retest_core(b, a, sess, jitter):
    out = []
    lv = _prev_session_levels(b, sess)
    for day, idxs in sess.items():
        if day not in lv or len(idxs) < 6:
            continue
        ph, pl, pc = lv[day]
        pv = _floor_pivots(ph, pl, pc)
        levels = [pv["R1"], pv["S1"]]
        if jitter:
            rng = max(ph - pl, 1e-9)
            seed = int(hashlib.md5(str(day).encode()).hexdigest()[:8], 16)
            off = (0.5 + (seed % 1000) / 1000.0) * rng     # 0.5..1.5 ranges
            sign = 1 if (seed >> 10) % 2 else -1
            levels = [x + sign * off for x in levels]
        for L in levels:
            state, brk_dir = "wait", None
            for i in idxs:
                u = a[i]
                if u <= 0:
                    continue
                c = b[i][4]
                if state == "wait":
                    if c > L + 0.1 * u:
                        state, brk_dir = "broken", "long"
                    elif c < L - 0.1 * u:
                        state, brk_dir = "broken", "short"
                elif state == "broken":
                    h, l = b[i][2], b[i][3]
                    if brk_dir == "long" and l <= L + 0.25 * u and c > L:
                        s = _clamp_atr(c - l, u)
                        if s:
                            out.append((i, "long", s, 2.0 * s))
                        state = "done"; break
                    if brk_dir == "short" and h >= L - 0.25 * u and c < L:
                        s = _clamp_atr(h - c, u)
                        if s:
                            out.append((i, "short", s, 2.0 * s))
                        state = "done"; break
                    if (brk_dir == "long" and c < L - 0.5 * u) or \
                       (brk_dir == "short" and c > L + 0.5 * u):
                        state = "wait"   # failed break; reset
    return out


# ------------------------------------------- geometry-matched controls

def _rnd(b, a, sess, satr, tatr, step=6, salt=""):
    out = []
    for i in range(30, len(b), step):
        seed = int(hashlib.md5((salt + str(b[i][0])).encode()).hexdigest()[:8], 16)
        out.append((i, "long" if seed % 2 else "short", satr, tatr))
    return out


def bk_random_20(b, a, sess):
    """Control matched to the 1.0/2.0 geometry (most models)."""
    return _rnd(b, a, sess, 1.0, 2.0, salt="g20|")


def bk_random_15(b, a, sess):
    """Control matched to Range spike's 1.0/1.5 geometry."""
    return _rnd(b, a, sess, 1.0, 1.5, salt="g15|")


BOOK_LIVE_MODELS = {
    "MA/ATR band":        bk_ma_atr_band,
    "Donchian SAR":       bk_donchian_sar,
    "Failure test":       bk_failure_test,
    "Break-retest pivot": bk_break_retest,
    "Break-retest RNDLVL": bk_break_retest_rndlvl,
    "RANDOM CTRL 2.0R":   bk_random_20,
    "RANDOM CTRL 1.5R":   bk_random_15,
}
