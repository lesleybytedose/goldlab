#!/usr/bin/env python3
"""
mult_edge.py - the last place an index edge could hide.

The price series is a martingale: no directional strategy on it can win.
But Deriv's MULTIPLIER contract adds a structure the price does not have -
loss is CAPPED at the stake when price moves 1/M against you.

On Boom/Crash, spikes are single-tick jumps. If a spike is LARGER than the
stop-out distance, the excess loss is absorbed by Deriv, not you. That is a
real asymmetry, and it is measurable.

 1. SPIKE vs STOP-OUT: for each multiplier tier, do spikes gap through?
 2. SIMULATION: short Boom / long Crash (against the spike direction),
    with the real loss cap, vs the same trade with UNCAPPED loss. The gap
    between them is the value of the cap.
 3. VERDICT vs a random-entry control on the same ticks.
"""
import os, random, statistics as st, sys

TICKS = os.path.expanduser("~/goldlab/data/ticks")
random.seed(77)
TIERS = [15, 20, 30, 40, 50, 100, 150, 200, 300, 400, 500, 1000]


def load(s):
    p = os.path.join(TICKS, s + "_ticks.csv")
    if not os.path.exists(p): return []
    r = []
    for ln in open(p):
        a = ln.strip().split(",")
        if len(a) == 2 and a[0].isdigit():
            try: r.append(float(a[1]))
            except Exception: pass
    return r


def sim(px, M, direction, hold, n_trials=3000, capped=True):
    """direction -1 = short. Returns mean profit per $1 stake."""
    out = []
    N = len(px)
    for _ in range(n_trials):
        i = random.randrange(200, N - hold - 2)
        entry = px[i]
        cut = 1.0 / M
        pnl = None
        for j in range(i + 1, i + 1 + hold):
            move = direction * (px[j] - entry) / entry      # + = in our favour
            if move <= -cut:
                pnl = -1.0 if capped else M * move          # cap vs true loss
                break
        if pnl is None:
            pnl = M * (direction * (px[i + hold] - entry) / entry)
        out.append(pnl)
    return sum(out) / len(out), out


def run(sym, spike_dir):
    px = load(sym)
    if len(px) < 20000: return
    d = [px[i] - px[i-1] for i in range(1, len(px))]
    ad = [abs(x) for x in d]
    med = st.median(ad)
    sp = [x for x in d if abs(x) > 10 * med]
    if not sp: return
    spike_pct = [abs(x) / px[0] for x in sp]
    msp = st.median(spike_pct)
    print("\n" + "=" * 66)
    print(sym + "   spikes go " + ("UP" if spike_dir > 0 else "DOWN")
          + "   median spike = " + str(round(100 * msp, 4)) + "% of price")

    print("  1. DOES A SPIKE GAP THROUGH THE STOP-OUT?")
    for M in TIERS:
        cut = 1.0 / M
        through = sum(1 for s in spike_pct if s > cut) / len(spike_pct)
        note = ""
        if through > 0.5:
            note = "   <-- cap absorbs part of the loss"
        print("     x" + str(M).ljust(5) + " stop-out at "
              + str(round(100 * cut, 4)) + "%   spikes exceeding it: "
              + str(round(100 * through, 1)) + "%" + note)

    print("  2. TRADE AGAINST THE SPIKE (collect the drift), hold 500 ticks")
    print("     mult    capped EV    uncapped EV    value of cap")
    trade_dir = -spike_dir            # short Boom, long Crash
    for M in (50, 100, 200, 300, 500, 1000):
        random.seed(77)
        ec, _ = sim(px, M, trade_dir, 500, capped=True)
        random.seed(77)
        eu, _ = sim(px, M, trade_dir, 500, capped=False)
        star = "  ** POSITIVE **" if ec > 0.02 else ""
        print("     x" + str(M).ljust(6)
              + ("%+.4f" % ec).rjust(10)
              + ("%+.4f" % eu).rjust(15)
              + ("%+.4f" % (ec - eu)).rjust(15) + star)

    print("  3. SAME TRADE, WITH THE SPIKE (lottery side)")
    for M in (100, 300, 1000):
        random.seed(77)
        ec, _ = sim(px, M, spike_dir, 500, capped=True)
        print("     x" + str(M).ljust(6) + ("%+.4f" % ec).rjust(10))


run("Boom_1000_Index", +1)
run("Crash_1000_Index", -1)
run("Boom_500_Index", +1)
run("Crash_500_Index", -1)
print("\n  EV is per $1 staked. 0.0000 = fair. Costs (spread) are NOT")
print("  included, so a result must clear roughly +0.01 to be real.")
