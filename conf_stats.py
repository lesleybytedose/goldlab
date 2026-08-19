#!/usr/bin/env python3
"""
conf_stats.py - does model agreement actually predict anything?

Joins confluence events to the signal log and asks two questions:
  1. do signals with 2+ models agreeing resolve better than solo signals?
  2. and how do the CONTROLS do at those same moments?

Question 2 is the one that matters. If coin flips also do better whenever
they happen to agree, then "confluence" is measuring market state, not
model insight.

  python3 conf_stats.py
"""
import json, os
from collections import defaultdict

HOME = os.path.expanduser("~/goldlab")
LOGS = os.path.join(HOME, "logs")


def rows(name):
    p = os.path.join(LOGS, name)
    out = []
    if os.path.exists(p):
        for ln in open(p):
            try: out.append(json.loads(ln))
            except Exception: pass
    return out


def mean(v):
    return sum(v) / len(v) if v else None


def main():
    sigs = [r for r in rows("live_signals.jsonl")
            if r.get("phase") == "forward" and r.get("R") is not None]
    conf = rows("confluence.jsonl")
    if not sigs:
        print("  no resolved forward signals yet"); return

    # events keyed by (sym, dir, candle)
    ev = {}
    for c in conf:
        key = (c.get("sym"), c.get("dir"), c.get("ts"))
        n_m = c.get("n_models", len(c.get("models", [])))
        n_c = c.get("n_controls", len(c.get("controls_agreeing", [])))
        prev = ev.get(key)
        if not prev or n_m > prev[0]:
            ev[key] = (n_m, n_c)

    buckets = defaultdict(list)     # models
    cbuckets = defaultdict(list)    # controls at the same moments
    for r in sigs:
        key = (r.get("sym"), r.get("dir"), r.get("ts"))
        n_m, n_c = ev.get(key, (1, 0))
        is_ctrl = "RANDOM" in r.get("model", "") or "RNDLVL" in r.get("model", "")
        tag = "2+ agree" if n_m >= 2 else "solo"
        (cbuckets if is_ctrl else buckets)[tag].append(r["R"])

    print(f"\n  confluence events logged: {len(ev)}")
    print("  " + "="*62)
    print(f"  {'group':<14}{'n':>7}{'mean R':>10}{'win %':>9}")
    print("  " + "-"*62)
    for tag in ("2+ agree", "solo"):
        v = buckets.get(tag, [])
        if v:
            print(f"  MODELS {tag:<8}{len(v):>6}{mean(v):>+10.3f}"
                  f"{100*sum(1 for x in v if x>0)/len(v):>8.1f}%")
    for tag in ("2+ agree", "solo"):
        v = cbuckets.get(tag, [])
        if v:
            print(f"  CTRL   {tag:<8}{len(v):>6}{mean(v):>+10.3f}"
                  f"{100*sum(1 for x in v if x>0)/len(v):>8.1f}%")
    print("  " + "-"*62)
    m2, m1 = buckets.get("2+ agree", []), buckets.get("solo", [])
    c2, c1 = cbuckets.get("2+ agree", []), cbuckets.get("solo", [])
    if len(m2) >= 30 and len(m1) >= 30:
        lift = mean(m2) - mean(m1)
        clift = (mean(c2) - mean(c1)) if (len(c2) >= 30 and len(c1) >= 30) else None
        print(f"\n  model lift from agreement:   {lift:+.3f}R")
        if clift is not None:
            print(f"  control lift at same moments:{clift:+.3f}R")
            print(f"  EXCESS over control:         {lift-clift:+.3f}R")
            print("\n  If the excess is near zero, agreement is measuring market")
            print("  state, not model insight. That is the finding, not a failure.")
        else:
            print("  (need 30+ control signals in each bucket for the comparison)")
    else:
        print(f"\n  not enough yet: {len(m2)} agreeing, {len(m1)} solo (need 30+ each)")
    print()


if __name__ == "__main__":
    main()
