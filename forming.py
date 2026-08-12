#!/usr/bin/env python3
"""
forming.py - which models would fire if this candle closed right now?

Loads the closed bars, appends the candle still forming, then runs the exact
same model functions live.py uses. Anything that fires on that last bar is a
setup in progress - you get told while there is still time to look at it.

Nothing here confirms a signal. A forming setup can evaporate in the last
minute of the candle, and often does.

  python3 forming.py            print what is forming
  python3 forming.py --json     machine readable
"""
import glob, json, os, sys
from datetime import datetime, timezone, timedelta

FEED_TZ = timezone.utc
SAST = timezone(timedelta(hours=2))
HOME = os.path.expanduser("~/goldlab")
RAW = os.path.join(HOME, "data/raw")
OUT = os.path.join(HOME, "logs", "forming.json")

sys.path.insert(0, HOME)
import live as L                       # the same models, not a copy


def with_forming(sym):
    """Closed bars plus the candle currently open, if the EA is sending it."""
    path = os.path.join(RAW, f"{sym}_M15_live.csv")
    if not os.path.exists(path):
        return None, None
    bars = L.load(path)
    if len(bars) < 200:
        return None, None
    fp = os.path.join(RAW, f"{sym}_M15_forming.json")
    if not os.path.exists(fp):
        return bars, None
    try:
        fb = json.load(open(fp))
        t = datetime.fromisoformat(f"{fb['d']} {fb['t']}").replace(tzinfo=FEED_TZ)
        if (datetime.now(FEED_TZ) - t).total_seconds() > 1800:
            return bars, None          # stale, ignore it
        row = [t, fb["o"], fb["h"], fb["l"], fb["c"], fb.get("s", 0)]
        if bars and bars[-1][0] == t:
            bars[-1] = row
        else:
            bars.append(row)
        return bars, t
    except Exception:
        return bars, None


def scan(sym):
    bars, ftime = with_forming(sym)
    if not bars or ftime is None:
        return []
    a = L.atr(bars)
    sess = L.sessions(bars)
    last = len(bars) - 1
    out = []
    for name, fn in L.MODELS.items():
        try:
            sigs = fn(bars, a, sess)
        except Exception:
            continue
        for i, d, satr, tatr in sigs:
            if i != last:              # only the candle still open
                continue
            u = a[i]
            if u <= 0:
                continue
            e = bars[i][4]
            out.append(dict(
                sym=sym, model=name, dir=d,
                entry=round(e, 5),
                stop=round(e - satr * u if d == "long" else e + satr * u, 5),
                target=round(e + tatr * u if d == "long" else e - tatr * u, 5),
                ts=ftime.isoformat(),
                closes=(ftime + timedelta(minutes=15)).astimezone(SAST).strftime("%H:%M"),
            ))
    return out


def main():
    syms = []
    for p in sorted(glob.glob(os.path.join(RAW, "*_M15_forming.json"))):
        syms.append(os.path.basename(p).split("_")[0])
    found = []
    for s in syms:
        found += scan(s)

    now = datetime.now(FEED_TZ)
    mins_left = 15 - (now.minute % 15)
    payload = dict(
        generated=now.isoformat(timespec="seconds"),
        minutes_left=mins_left,
        forming=found,
    )
    try:
        json.dump(payload, open(OUT, "w"))
    except Exception:
        pass

    if "--json" in sys.argv:
        print(json.dumps(payload, indent=1)); return

    print(f"\n  FORMING NOW   {datetime.now(SAST):%H:%M} SAST"
          f"   candle closes in ~{mins_left} min")
    print("=" * 74)
    if not found:
        print("  Nothing forming. No model would fire if this candle closed now.\n")
        return
    for f in found:
        arrow = "^" if f["dir"] == "long" else "v"
        print(f"  {arrow} {f['model']:20} {f['dir']:5} {f['sym']:12} "
              f"entry {f['entry']:>10}  stop {f['stop']:>10}")
    print("=" * 74)
    print("  These are provisional. A setup can vanish in the last minute of\n"
          "  the candle. Nothing counts until the close.\n")


if __name__ == "__main__":
    main()
