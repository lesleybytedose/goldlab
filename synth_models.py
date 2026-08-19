#!/usr/bin/env python3
"""
synth_models.py - models whose premise actually exists on a generated index.

WHAT THE PROBES ESTABLISHED (2026-08-19)
  * V75: variance ratios ~1.0, no autocorrelation, runs consistent with
    independent moves. A constant-volatility random walk.
  * Boom/Crash: spike timing memoryless, spike size independent of wait,
    net drift not established once error bars are honest.
  * Accumulators: negative EV by barrier arithmetic.

  So NO model here is expected to win. These exist because "expected" is
  not "measured", and running them costs nothing but CPU. Each is judged
  against the coin flip at its own geometry, like everything else.

WHY THESE AND NOT OTHERS
  Every model below is built only from volatility, range and the jump
  mechanic. None reads a session, a pivot, a prior-day close or a stop
  cluster, because none of those exist on a generated series.

  All use 1.0 stop / 2.0 target so RANDOM CTRL 2.0R is the matched
  control. Do not change the geometry without adding a matching control.

Wire-up (live.py):
    from synth_models import SYNTH_MODELS, SYNTH_MODEL_NAMES
    MODELS.update(SYNTH_MODELS)
    SYNTH_OK |= SYNTH_MODEL_NAMES
"""


def _sma(b, i, n):
    if i + 1 < n:
        return None
    return sum(x[4] for x in b[i - n + 1:i + 1]) / n


def sy_spike_fade(b, a, sess, mult=4.0, cool=3):
    """THE BLEED HARVESTER.

    Boom jumps up then resumes bleeding down; Crash jumps down then
    resumes drifting up. The jump is untradeable (memoryless, and it is
    over inside one bar) but the BLEED is the persistent half of the
    design. So: after an outsized bar, trade AGAINST it - i.e. with the
    bleed - and let the next spike be the risk.

    This is the honest version of "trade Boom": it does not try to catch
    the spike, it collects the drift the spike is paid for. If the two
    halves net to zero as designed, this loses to its control. That is
    the test.
    """
    out = []
    last = -99
    for i in range(30, len(b)):
        if a[i] <= 0 or i - last < cool:
            continue
        rng = b[i][2] - b[i][3]
        if rng < mult * a[i]:
            continue
        up = b[i][4] > b[i][1]
        out.append((i, "short" if up else "long", 1.0, 2.0))
        last = i
    return out


def sy_donchian_fade(b, a, sess, p=40):
    """Fade the channel extreme. On a driftless random walk an extreme
    carries no continuation information, so fading it should be exactly
    as good as following it - which is precisely what makes it a clean
    test of whether the series is really driftless."""
    out = []
    for i in range(p + 2, len(b)):
        if a[i] <= 0:
            continue
        hi = max(x[2] for x in b[i - p:i])
        lo = min(x[3] for x in b[i - p:i])
        c = b[i][4]
        if c > hi:
            out.append((i, "short", 1.0, 2.0))
        elif c < lo:
            out.append((i, "long", 1.0, 2.0))
    return out


def sy_band_revert(b, a, sess, n=20, k=2.0):
    """Bollinger-style reversion: close beyond mean +/- k*sd, trade back
    toward the mean. Constant designed volatility is the one property V75
    genuinely has, so a band built from it is at least measuring
    something real about the generator."""
    out = []
    for i in range(n + 2, len(b)):
        if a[i] <= 0:
            continue
        w = [x[4] for x in b[i - n + 1:i + 1]]
        m = sum(w) / n
        var = sum((x - m) ** 2 for x in w) / n
        sd = var ** 0.5
        if sd <= 0:
            continue
        c = b[i][4]
        if c > m + k * sd:
            out.append((i, "short", 1.0, 2.0))
        elif c < m - k * sd:
            out.append((i, "long", 1.0, 2.0))
    return out


def sy_streak_fade(b, a, sess, run=6, min_atr=2.0, cool=6):
    """Fade an extended one-way run.

    A bleeding index is down-closing on most bars, so "N down closes" is
    nearly always true and would make this model "always long". The run
    is therefore measured close-to-close AND must cover min_atr of
    ground, with a cooldown so one long slide is not counted many times.

    The runs test said direction is independent, so this should be
    worthless. It is here as a falsification check on that test.
    """
    out = []
    last = -99
    for i in range(run + 3, len(b)):
        if a[i] <= 0 or i - last < cool:
            continue
        ups = sum(1 for k in range(i - run + 1, i + 1) if b[k][4] > b[k - 1][4])
        moved = abs(b[i][4] - b[i - run][4])
        if moved < min_atr * a[i]:
            continue
        if ups == run:
            out.append((i, "short", 1.0, 2.0)); last = i
        elif ups == 0:
            out.append((i, "long", 1.0, 2.0)); last = i
    return out


def sy_vol_compress(b, a, sess, look=20, tight=1.6):
    """Compression then expansion. A statement about the variance process
    only - no participants required. If volatility really is constant by
    design, compression is noise and this must fail."""
    out = []
    for i in range(look + 5, len(b)):
        if a[i] <= 0:
            continue
        w = b[i - look:i]
        hi = max(x[2] for x in w)
        lo = min(x[3] for x in w)
        if (hi - lo) > tight * a[i] * 2:
            continue
        c = b[i][4]
        if c > hi:
            out.append((i, "long", 1.0, 2.0))
        elif c < lo:
            out.append((i, "short", 1.0, 2.0))
    return out


SYNTH_MODELS = {
    "Spike fade (bleed)": sy_spike_fade,
    "Donchian fade":      sy_donchian_fade,
    "Band revert":        sy_band_revert,
    "Vol compress":       sy_vol_compress,
    "Streak fade":        sy_streak_fade,
}
SYNTH_MODEL_NAMES = set(SYNTH_MODELS)
