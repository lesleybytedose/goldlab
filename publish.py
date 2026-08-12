#!/usr/bin/env python3
"""
publish.py - gathers everything the app needs into one JSON file.
Runs from cron alongside signals.py.

Writes:  /opt/hgs/client/goldlab/state.json
"""
import csv, glob, json, os, subprocess
from datetime import datetime, timezone, timedelta

SAST = timezone(timedelta(hours=2))
HOME = os.path.expanduser("~/goldlab")
RAW = os.path.join(HOME, "data/raw")
LOGS = os.path.join(HOME, "logs")
OUT = "/opt/hgs/client/goldlab/state.json"
FALLBACK = os.path.join(HOME, "web", "state.json")


def feed_health():
    feeds = []
    now = datetime.now(SAST)
    for p in sorted(glob.glob(os.path.join(RAW, "*_M15_live.csv"))):
        sym = os.path.basename(p).split("_")[0]
        try:
            rows = 0
            last = None
            with open(p) as f:
                for line in f:
                    rows += 1
                    last = line
            parts = last.strip().split(",")
            ts = datetime.fromisoformat(f"{parts[0]} {parts[1]}").replace(tzinfo=SAST)
            age = (now - ts).total_seconds() / 60
            feeds.append(dict(symbol=sym, bars=rows - 1,
                              last=ts.strftime("%Y-%m-%d %H:%M"),
                              age_min=round(age),
                              stale=age > 45,
                              spread=float(parts[6]) if len(parts) > 6 else None))
        except Exception:
            feeds.append(dict(symbol=sym, bars=0, last=None, age_min=None, stale=True))
    return feeds


def scoreboard():
    p = os.path.join(LOGS, "signals.jsonl")
    if not os.path.exists(p):
        return dict(backfill=None, forward=None, recent=[])
    rows = []
    for ln in open(p):
        try:
            rows.append(json.loads(ln))
        except Exception:
            pass

    def summarise(subset):
        done = [r for r in subset if r.get("R") is not None]
        if not done:
            return dict(logged=len(subset), resolved=0)
        R = [r["R"] for r in done]
        w = [x for x in R if x > 0]
        return dict(logged=len(subset), resolved=len(done),
                    win=round(len(w) / len(R), 3),
                    exp=round(sum(R) / len(R), 3),
                    total=round(sum(R), 2))

    back = [r for r in rows if r.get("phase") == "backfill"]
    fwd = [r for r in rows if r.get("phase") != "backfill"]
    recent = sorted(fwd, key=lambda r: r.get("ts", ""), reverse=True)[:12]
    return dict(backfill=summarise(back), forward=summarise(fwd), recent=recent)


def live_state():
    """Current market state from the freshest gold feed."""
    p = os.path.join(RAW, "XAUUSDc_M15_live.csv")
    if not os.path.exists(p):
        return None
    rows = list(csv.DictReader(open(p)))
    if len(rows) < 40:
        return None
    tail = rows[-40:]
    today = rows[-1]["date"]
    session = [r for r in rows if r["date"] == today]
    hi = max(float(r["high"]) for r in session)
    lo = min(float(r["low"]) for r in session)
    c = float(rows[-1]["close"])
    opens = session[:8]
    watching = None
    if len(opens) >= 8:
        ohi = max(float(r["high"]) for r in opens)
        olo = min(float(r["low"]) for r in opens)
        if c > ohi:
            watching = "above the opening range"
        elif c < olo:
            watching = "below the opening range"
        else:
            watching = "inside the opening range"
    return dict(price=c, day_high=hi, day_low=lo, bars_today=len(session),
                watching=watching, spread=float(rows[-1].get("spread") or 0),
                last=f"{rows[-1]['date']} {rows[-1]['time']}")


def services():
    out = {}
    for svc in ("goldlab", "goldlab-ingest"):
        try:
            r = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True, timeout=5)
            out[svc] = r.stdout.strip()
        except Exception:
            out[svc] = "unknown"
    return out


def observations():
    p = os.path.join(LOGS, "observations.jsonl")
    n = 0
    if os.path.exists(p):
        n = sum(1 for _ in open(p))
    return dict(count=n, required=30)


def main():
    state = dict(
        generated=datetime.now(SAST).isoformat(timespec="seconds"),
        feeds=feed_health(),
        signals=scoreboard(),
        live=live_state(),
        services=services(),
        observations=observations(),
    )
    blob = json.dumps(state, indent=1)
    written = []
    for path in (OUT, FALLBACK):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(blob)
            written.append(path)
        except Exception:
            pass
    print(f"  published -> {', '.join(written) or 'nowhere (check permissions)'}")


if __name__ == "__main__":
    main()
