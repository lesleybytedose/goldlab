#!/usr/bin/env python3
"""
accu_final.py - the accumulator verdict done right, and BMM's claim tested
with a control.

A) FAIR BARRIER, EMPIRICAL. For every symbol we hold ticks for, the exact
   barrier that makes each growth rate break even - computed by counting
   real moves, not by assuming a distribution. Reported as % of spot and
   in sigma units so it can be compared across symbols.

   Then plug in what the platform actually shows:
     python3 accu_final.py barrier 1HZ100V 0.0459 0.01
   (symbol, barrier as % of previous spot, growth rate)
   -> exact P(inside), EV per tick, EV at every hold length. No assumptions.

B) BMM: "BOOM 1000 spikes mostly after 45 ticks after a huge drop."
   A huge drop on Boom is CUMULATIVE - its single ticks are all tiny. So:
   find windows with the largest cumulative declines, measure ticks to the
   next spike, and compare against random start points on the same series.
"""
import math, os, random, statistics as st, sys

TICKS = os.path.expanduser("~/goldlab/data/ticks")
random.seed(4242)
DEF = ["1HZ100V", "Volatility_75_Index", "Volatility_100_Index",
       "Volatility_25_Index", "Volatility_10_Index",
       "Boom_1000_Index", "Crash_1000_Index"]
GROWTHS = (0.01, 0.02, 0.03, 0.04, 0.05)


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


def relmoves(px):
    """|move| as % of the PREVIOUS spot - exactly how the barrier is defined."""
    return [abs(px[i] - px[i-1]) / px[i-1] * 100.0 for i in range(1, len(px))]


def q(xs, p):
    s = sorted(xs)
    return s[min(len(s)-1, int(p * (len(s)-1)))]


def fair_table():
    print("A) FAIR BARRIER PER GROWTH RATE  (empirical, % of previous spot)")
    print("   the barrier that makes EV exactly 1.0000. Deriv's real barrier")
    print("   WIDER than this = your edge. NARROWER = house edge.\n")
    for sym in DEF:
        px = load(sym)
        if len(px) < 20000: continue
        rel = relmoves(px)
        sd = st.pstdev([(px[i]-px[i-1])/px[i-1]*100.0 for i in range(1, len(px))])
        print("   " + sym + "   " + str(len(rel)) + " ticks   sigma "
              + str(round(sd, 6)) + "%")
        for g in GROWTHS:
            need = 1.0/(1.0+g)
            b = q(rel, need)
            print("      " + (str(int(g*100)) + "%").rjust(4)
                  + "  fair barrier " + ("%.6f%%" % b).rjust(12)
                  + "   = " + ("%.3f" % (b/sd if sd else 0)).rjust(6) + " sigma")
        print()


def barrier_check(sym, barrier_pct, growth):
    px = load(sym)
    if len(px) < 20000:
        print("   no ticks for " + sym); return
    rel = relmoves(px)
    n = len(rel)
    inside = sum(1 for x in rel if x <= barrier_pct) / n
    need = 1.0/(1.0+growth)
    ev = (1.0+growth) * inside
    fair_b = q(rel, need)
    print("\n" + "="*66)
    print("   " + sym + "   " + str(n) + " real tick moves")
    print("   platform barrier : " + str(barrier_pct) + "% of previous spot")
    print("   fair barrier     : " + ("%.6f" % fair_b) + "%")
    print("   gap              : " + ("%+.2f" % (100*(barrier_pct/fair_b - 1)))
          + "%  (" + ("WIDER - your edge" if barrier_pct > fair_b
                      else "NARROWER - house edge") + ")")
    print()
    print("   P(tick inside)   : " + ("%.4f" % (100*inside)) + "%"
          + "     needed " + ("%.4f" % (100*need)) + "%")
    print("   EV per tick      : " + ("%.6f" % ev)
          + "   (" + ("%+.4f" % ((ev-1)*100)) + "% per tick)")
    print()
    print("   hold      EV per $1     result on a $20 stake")
    for k in (1, 3, 10, 25, 45, 55, 75, 120, 230):
        e = ev**k
        print("   " + str(k).rjust(4) + " ticks   " + ("%.4f" % e).rjust(9)
              + "        " + ("%+.2f" % (20*(e-1))).rjust(8)
              + ("   <-- POSITIVE" if e > 1.0 else ""))
    print()
    print("   (this counts real moves against the real barrier - no model,")
    print("    no simulation, no distribution assumed)")


def bmm(sym="Boom_1000_Index"):
    px = load(sym)
    print("\n" + "="*66)
    print("B) BMM: 'BOOM 1000 spikes mostly after 45 ticks after a huge drop'")
    if len(px) < 20000:
        print("   no ticks"); return
    d = [px[i]-px[i-1] for i in range(1, len(px))]
    med = st.median([abs(x) for x in d])
    spikes = sorted(i for i, x in enumerate(d) if x > 10*med)
    print("   " + str(len(spikes)) + " up-spikes in " + str(len(d)) + " ticks"
          + "   (1 per " + str(round(len(d)/max(1,len(spikes)))) + ")")
    print("   NOTE: Boom has no single-tick 'huge drops' - its down moves are a")
    print("   steady dribble. A drop on a chart is CUMULATIVE, so we use that.\n")

    def next_spike(i):
        for s in spikes:
            if s > i: return s - i
        return None

    for W in (25, 50, 100):
        cum = []
        for i in range(W, len(d)-300):
            cum.append((sum(d[i-W:i]), i))
        if len(cum) < 500: continue
        cum.sort()
        worst = [i for _, i in cum[:max(30, len(cum)//20)]]     # biggest 5% declines
        gaps = [g for g in (next_spike(i) for i in worst) if g]
        ctl_pts = random.sample(range(W, len(d)-300), min(3000, len(d)-W-300))
        cgaps = [g for g in (next_spike(i) for i in ctl_pts) if g]
        if not gaps or not cgaps: continue
        gaps.sort(); cgaps.sort()
        win = lambda xs: sum(1 for x in xs if 35 <= x <= 55)/len(xs)
        print("   drop window " + str(W) + " ticks   (" + str(len(gaps))
              + " biggest declines vs " + str(len(cgaps)) + " random points)")
        print("      ticks to next spike   median " + str(gaps[len(gaps)//2]).rjust(5)
              + "   control " + str(cgaps[len(cgaps)//2]).rjust(5))
        print("      landing in 35-55      " + ("%.1f%%" % (100*win(gaps))).rjust(7)
              + "   control " + ("%.1f%%" % (100*win(cgaps))).rjust(7))
        ratio = win(gaps)/win(cgaps) if win(cgaps) else 0
        print("      ratio " + ("%.2f" % ratio)
              + ("   <-- SUPPORTED" if ratio > 2 else "   (1.00 = no effect)"))
    print()
    print("   Also testing 'baby spikes delay the big spike':")
    mids = [i for i, x in enumerate(d) if 3*med < x <= 10*med]
    if len(mids) >= 30:
        g1 = [g for g in (next_spike(i) for i in mids) if g]
        ctl = random.sample(range(50, len(d)-300), min(3000, len(d)-350))
        g2 = [g for g in (next_spike(i) for i in ctl) if g]
        if g1 and g2:
            print("      after a baby spike: median " + str(sorted(g1)[len(g1)//2])
                  + " ticks to the next big spike")
            print("      random point:       median " + str(sorted(g2)[len(g2)//2])
                  + " ticks   (" + str(len(mids)) + " baby spikes found)")
    else:
        print("      only " + str(len(mids)) + " mid-sized moves - no baby-spike class exists")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "barrier" and len(a) >= 4:
        barrier_check(a[1], float(a[2]), float(a[3]))
    elif a and a[0] == "bmm":
        bmm()
    else:
        fair_table(); bmm()
