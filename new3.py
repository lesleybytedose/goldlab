#!/usr/bin/env python3
"""
new3.py - append the three screened nominees to book_models_live.py.

Promoted 2026-08-19 after screen_all.py: these are the only three that
cleared BOTH the best-of-12 random bar AND the measured spread hurdle on
a symbol we actually feed. Fifteen other models screened; twelve nominated
nowhere, and the rest cleared the random bar but not the cost.

Declined and why:
  BOS_FVG      0/9 symbols, samples up to 4592. The fair-value-gap idea
               has no in-sample edge on any instrument we hold.
  KHOO_BOUNCE  1/9, GMMA 1/8, FIB_PULLBACK 1/9  - one hit in nine is the
               rate chance produces.
  KHOO_TC      positive on BOTH dead FX feeds (+0.21R n=918, +0.25R
               n=2102) and nowhere else. Logged as a curiosity; cannot be
               forward-tested while those feeds are off.
  PDC_OPEN     +0.006R on XAUUSDc - below the 3.5% spread hurdle.

These start at Stage 1 with the holdout sealed, like everything else.
"""
import os

p = os.path.expanduser("~/goldlab/book_models_live.py")
src = open(p).read()
if "bk_liq_sweep" in src:
    print("  already added"); raise SystemExit

src += '''


# ------------------------------------- screened nominees, 2026-08-19
# From screen_all.py: 18 models x 9 symbols. Only these three beat the
# best of 12 random variants AND cleared the measured spread cost on a
# live symbol. See the module docstring of new3.py for what was declined.

def bk_liq_sweep(b, a, sess):
    """Equal highs/lows form a liquidity pool, price sweeps it and closes
    back inside. Needs TWO touches building the level - that is what makes
    it different from CRT sweep, which needs only one extreme.
    Screened: Crash_1000 +0.317R n=77, XAUUSDc +0.018R n=1789."""
    out = []
    for i in range(40, len(b)):
        u = a[i]
        if u <= 0:
            continue
        w = b[i - 20:i]
        hs = sorted((x[2] for x in w), reverse=True)[:3]
        ls = sorted(x[3] for x in w)[:3]
        if len(hs) == 3 and (hs[0] - hs[2]) < 0.25 * u:
            lvl = hs[0]
            if b[i][2] > lvl and b[i][4] < lvl:
                out.append((i, "short", 1.0, 2.0))
                continue
        if len(ls) == 3 and (ls[2] - ls[0]) < 0.25 * u:
            lvl = ls[0]
            if b[i][3] < lvl and b[i][4] > lvl:
                out.append((i, "long", 1.0, 2.0))
    return out


def bk_tf_channel(b, a, sess):
    """Close beyond the 20-bar channel while the 50-bar mean is sloping the
    same way. Trend-following, with the alignment gate Bansal insists on.
    Screened: Crash_1000 +0.159R n=876, Boom_1000 +0.117R n=856."""
    out = []
    for i in range(60, len(b)):
        u = a[i]
        if u <= 0:
            continue
        hi = max(x[2] for x in b[i - 20:i])
        lo = min(x[3] for x in b[i - 20:i])
        s50 = sum(x[4] for x in b[i - 49:i + 1]) / 50
        s50p = sum(x[4] for x in b[i - 50:i]) / 50
        c = b[i][4]
        if c > hi and s50 > s50p:
            out.append((i, "long", 1.0, 2.0))
        elif c < lo and s50 < s50p:
            out.append((i, "short", 1.0, 2.0))
    return out


def bk_keltner(b, a, sess):
    """Price stretched outside a 2.25-ATR Keltner band, then pulls back to
    the mid-line and holds it, with the mid-line sloping the right way.
    Screened: XAUUSDc +0.127R n=243, Crash_1000 +0.474R n=83."""
    out = []
    for i in range(60, len(b)):
        u = a[i]
        if u <= 0:
            continue
        n = 20
        m = sum(x[4] for x in b[i - n + 1:i + 1]) / n
        mp = sum(x[4] for x in b[i - n:i]) / n
        up, dn = m + 2.25 * u, m - 2.25 * u
        was_up = any(b[k][4] > up for k in range(i - 6, i))
        was_dn = any(b[k][4] < dn for k in range(i - 6, i))
        if was_up and m > mp and b[i][3] <= m and b[i][4] > m:
            out.append((i, "long", 1.0, 2.0))
        elif was_dn and m < mp and b[i][2] >= m and b[i][4] < m:
            out.append((i, "short", 1.0, 2.0))
    return out


BOOK_LIVE_MODELS.update({
    "Liquidity sweep":  bk_liq_sweep,
    "TF channel":       bk_tf_channel,
    "Keltner pullback": bk_keltner,
})
'''
open(p, "w").write(src)
import ast; ast.parse(src)
print("  book_models_live.py: 3 models appended")
