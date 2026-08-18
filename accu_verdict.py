#!/usr/bin/env python3
"""
accu_verdict.py - the decisive accumulator test, plus BMM's one testable claim.

A) HOUSE EDGE, MEASURED. Deriv publishes exact barriers for Volatility 100
   (1s): 1% growth = +/-0.0064867741% of the previous spot, 5% growth =
   +/-0.0049358253%. We fetch that exact index (1HZ100V), measure how often
   a real tick move exceeds those barriers, and compute EV per $1.
   No assumptions, no simulation: just counting.

B) BMM CLAIM: "BOOM 1000 spikes mostly after 45 ticks after a huge drop."
   We find every large drop and measure the distribution of ticks-to-spike,
   against the memoryless baseline.

  python3 accu_verdict.py fetch      pull 1HZ100V ticks
  python3 accu_verdict.py verdict    run both tests
"""
import json, math, os, ssl, statistics as st, sys, time

OUT = os.path.expanduser("~/goldlab/data/ticks")
WS = "wss://ws.derivws.com/websockets/v3?app_id=1089"
BARRIERS = {0.01: 0.0064867741, 0.02: None, 0.03: None,
            0.04: None, 0.05: 0.0049358253}     # published values only


def fetch(sym="1HZ100V", target=100000):
    from websocket import create_connection
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, sym + "_ticks.csv")
    have = {}
    if os.path.exists(path):
        for ln in open(path):
            a = ln.strip().split(",")
            if len(a) == 2 and a[0].isdigit(): have[int(a[0])] = a[1]
    ws = create_connection(WS, timeout=30)
    end, got = "latest", 0
    try:
        while got < target:
            ws.send(json.dumps({"ticks_history": sym, "end": end, "count": 5000,
                                "style": "ticks", "adjust_start_time": 1}))
            r = json.loads(ws.recv())
            if "error" in r:
                print("  " + str(r["error"].get("message"))); break
            h = r.get("history") or {}
            times, prices = h.get("times") or [], h.get("prices") or []
            if not times: break
            new = 0
            for t, px in zip(times, prices):
                if t not in have: have[t] = str(px); new += 1
            got += new
            print("  " + sym + ": +" + str(new) + " (total " + str(len(have)) + ")", flush=True)
            if new == 0: break
            end = str(min(times) - 1); time.sleep(0.4)
    finally:
        ws.close()
    with open(path, "w") as f:
        f.write("epoch,price\n")
        for t in sorted(have): f.write(str(t) + "," + have[t] + "\n")
    print("  saved " + str(len(have)) + " ticks")


def load(sym):
    p = os.path.join(OUT, sym + "_ticks.csv")
    if not os.path.exists(p): return []
    r = []
    for ln in open(p):
        a = ln.strip().split(",")
        if len(a) == 2 and a[0].isdigit():
            try: r.append(float(a[1]))
            except Exception: pass
    return r


def house_edge():
    px = load("1HZ100V")
    print("\n" + "=" * 68)
    print("A) HOUSE EDGE ON DERIV'S OWN PUBLISHED BARRIERS  (V100 1s Index)")
    if len(px) < 20000:
        print("   no 1HZ100V ticks - run: python3 accu_verdict.py fetch")
        return
    # relative moves: barrier is a % of the PREVIOUS spot
    rel = [abs(px[i] - px[i-1]) / px[i-1] * 100.0 for i in range(1, len(px))]
    n = len(rel)
    print("   " + str(n) + " tick moves   median " + str(round(st.median(rel), 7))
          + "%   sd " + str(round(st.pstdev(rel), 7)) + "%")
    print()
    print("   growth   barrier(%)     P(inside)   needed     EV/tick    EV per $1")
    for g in sorted(BARRIERS):
        b = BARRIERS[g]
        if b is None: continue
        inside = sum(1 for x in rel if x <= b) / n
        need = 1.0 / (1.0 + g)
        ev_tick = (1 + g) * inside
        best = max((inside ** k) * ((1 + g) ** k) for k in range(1, 231))
        flag = "  <-- POSITIVE" if best > 1.02 else ""
        print("   " + (str(int(g * 100)) + "%").rjust(5)
              + str(b).rjust(15)
              + (str(round(100 * inside, 4)) + "%").rjust(13)
              + (str(round(100 * need, 4)) + "%").rjust(11)
              + str(round(ev_tick, 6)).rjust(11)
              + str(round(best, 4)).rjust(11) + flag)
    print()
    print("   EV/tick below 1.000000 means every tick held is negative expectancy,")
    print("   and the loss compounds. Above 1.000000 would be a real edge.")


def bmm_claim():
    px = load("Boom_1000_Index")
    print("\n" + "=" * 68)
    print("B) BMM CLAIM: 'BOOM 1000 spikes mostly after 45 ticks after a huge drop'")
    if len(px) < 20000:
        print("   no Boom ticks"); return
    d = [px[i] - px[i-1] for i in range(1, len(px))]
    med = st.median([abs(x) for x in d])
    spikes = [i for i, x in enumerate(d) if x > 10 * med]
    drops = [i for i, x in enumerate(d) if x < -5 * med]
    print("   " + str(len(spikes)) + " up-spikes, " + str(len(drops))
          + " 'huge drops' (< -5x median)")
    if len(drops) < 20:
        print("   too few drops to judge"); return
    gaps = []
    for i in drops:
        nxt = [s for s in spikes if s > i]
        if nxt: gaps.append(nxt[0] - i)
    if not gaps:
        print("   no spikes followed a drop"); return
    gaps.sort()
    mean = sum(gaps) / len(gaps)
    print("   ticks from a huge drop to the next spike:")
    print("     median " + str(gaps[len(gaps)//2]) + "   mean " + str(round(mean, 1))
          + "   min " + str(min(gaps)) + "   max " + str(max(gaps)))
    near45 = sum(1 for g in gaps if 35 <= g <= 55) / len(gaps)
    base = len(spikes) / len(d)
    exp45 = 21 * base            # memoryless expectation for a 21-tick window
    print("     within 35-55 ticks: " + str(round(100 * near45, 1)) + "%")
    print("     memoryless would predict: " + str(round(100 * exp45, 1)) + "%")
    verdict = ("SUPPORTED" if near45 > 3 * exp45 and near45 > 0.15
               else "NOT SUPPORTED - consistent with random timing")
    print("     => " + verdict)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "verdict"
    if cmd == "fetch":
        fetch()
    else:
        house_edge(); bmm_claim()
