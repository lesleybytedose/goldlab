#!/usr/bin/env python3
"""
monitor.py - one screen: are feeds alive, are alerts flowing, is anything
stuck? Read-only. Run it any time, or from cron with --alert to be told
when something breaks.

  python3 monitor.py           print the dashboard
  python3 monitor.py --alert   also send a Telegram/WhatsApp message IF
                               something is wrong (silent when healthy)
"""
import json, os, subprocess, sys, time
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~/goldlab")
RAW = os.path.join(HOME, "data/raw")
LOGS = os.path.join(HOME, "logs")
UTC = timezone.utc
SAST = timezone(timedelta(hours=2))

# symbols no longer fed - not a fault if they are silent
DEAD = ("XAUUSDc", "EURUSDc", "EURGBPc")
# gold sleeps at the weekend and around daily settlement; 247/synthetics do not
ALWAYS_ON = ("Volatility_75_Index", "Boom_1000_Index", "Crash_1000_Index",
             "XAUUSD247m")

problems = []


def age_min(ts):
    return (datetime.now(UTC) - ts).total_seconds() / 60


def feeds():
    print("  FEEDS")
    print("  " + "-"*66)
    import glob
    for p in sorted(glob.glob(os.path.join(RAW, "*_M15_live.csv"))):
        sym = os.path.basename(p).replace("_M15_live.csv", "")
        if sym in DEAD:
            continue
        lines = open(p).read().splitlines()
        if len(lines) < 2:
            print(f"    {sym:22} EMPTY"); problems.append(f"{sym} empty"); continue
        last = lines[-1].split(",")
        try:
            t = datetime.fromisoformat(last[0] + " " + last[1]).replace(tzinfo=UTC)
            a = age_min(t)
        except Exception:
            print(f"    {sym:22} unreadable last row"); continue
        vol = last[7] if len(last) > 7 else "-"
        volok = "vol" if vol not in ("-", "0") else "NO VOL"
        mark = "ok"
        if sym in ALWAYS_ON and a > 40:
            mark = "STALE"; problems.append(f"{sym} stale {a:.0f}m")
        elif a > 40:
            mark = "quiet (session closed?)"
        print(f"    {sym:22} {len(lines)-1:>7} bars  last {a:>5.0f}m ago  "
              f"{volok:>7}  {mark}")


def signals():
    print("\n  SIGNALS")
    print("  " + "-"*66)
    p = os.path.join(LOGS, "live_signals.jsonl")
    if not os.path.exists(p):
        print("    no signal log"); problems.append("no signal log"); return
    fwd = []
    for ln in open(p):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("phase") == "forward":
            fwd.append(r)
    if not fwd:
        print("    no forward signals"); return
    fwd.sort(key=lambda r: r.get("ts", ""))
    newest = fwd[-1]
    try:
        t = datetime.fromisoformat(newest["ts"])
        a = age_min(t)
    except Exception:
        a = None
    res = sum(1 for r in fwd if r.get("R") is not None)
    op = len(fwd) - res
    print(f"    {len(fwd):,} forward   {res:,} resolved   {op:,} open")
    if a is not None:
        print(f"    newest: {newest['model']} {newest['dir']} on "
              f"{newest['sym']}  {a:.0f}m ago")
        if a > 240:
            problems.append(f"no new signal for {a/60:.1f}h")
    # last 24h by hour
    cut = time.time() - 86400
    n24 = 0
    for r in fwd:
        try:
            if datetime.fromisoformat(r["ts"]).timestamp() > cut:
                n24 += 1
        except Exception:
            pass
    print(f"    last 24h: {n24} signals")
    if n24 == 0:
        problems.append("zero signals in 24h")


def alerts():
    print("\n  ALERTS")
    print("  " + "-"*66)
    seen = os.path.join(LOGS, "alerts_seen.json")
    if os.path.exists(seen):
        m = age_min(datetime.fromtimestamp(os.path.getmtime(seen), UTC))
        try:
            n = len(json.load(open(seen)))
        except Exception:
            n = "?"
        print(f"    {n} alerts remembered, last activity {m:.0f}m ago")
        if m > 720:
            problems.append(f"no alert activity for {m/60:.1f}h")
    else:
        print("    no alert history yet"); problems.append("no alert history")
    cfg = os.path.join(HOME, "alerts.json")
    try:
        c = json.load(open(cfg))
        chans = []
        if c.get("telegram_token"): chans.append("telegram")
        n_wa = (1 if c.get("wa_key") else 0) + len(c.get("wa_extra", []))
        if n_wa: chans.append(f"whatsapp x{n_wa}")
        if c.get("smtp_host"): chans.append("email")
        print(f"    channels: {', '.join(chans) if chans else 'NONE'}")
        if not chans:
            problems.append("no alert channels configured")
    except Exception:
        print("    alerts.json unreadable"); problems.append("alerts.json unreadable")


def bot():
    print("\n  DEMO BOT")
    print("  " + "-"*66)
    f = os.path.join(LOGS, "demo_fills.jsonl")
    if not os.path.exists(f):
        print("    no fills ever — bot polling but not executing")
        return
    rows = []
    for ln in open(f):
        try: rows.append(json.loads(ln))
        except Exception: pass
    v110 = [r for r in rows if r.get("ea") == "1.10"]
    print(f"    {len(rows)} fills total, {len(v110)} since the v1.10 fix")
    if v110:
        last = v110[-1]
        print(f"    last: {last.get('model')} {last.get('dir')} "
              f"{last.get('lots')} lots  slip "
              f"{(last.get('fill',0)-last.get('signal',0)):+.3f}")
    lock = os.path.join(LOGS, "served_lock.json")
    if os.path.exists(lock):
        try: print(f"    {len(json.load(open(lock)))} signal(s) currently offered to the bot")
        except Exception: pass


def plumbing():
    print("\n  PLUMBING")
    print("  " + "-"*66)
    for svc in ("goldlab", "goldlab-ingest"):
        try:
            r = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True, timeout=5)
            st = r.stdout.strip() or "unknown"
        except Exception:
            st = "unknown"
        print(f"    {svc:16} {st}")
        if st != "active":
            problems.append(f"service {svc} is {st}")
    st = os.path.join(HOME, "web", "state.json")
    if os.path.exists(st):
        m = age_min(datetime.fromtimestamp(os.path.getmtime(st), UTC))
        print(f"    state.json      {m:.0f}m old")
        if m > 10:
            problems.append(f"state.json stale ({m:.0f}m)")
        try:
            ev = json.load(open(st)).get("evaluation", {})
            print(f"    evaluation      dev {ev.get('dev_n',0):,} / "
                  f"holdout {ev.get('holdout_n',0):,}  "
                  f"candidates {len(ev.get('candidates',[]))}")
        except Exception:
            pass
    else:
        problems.append("state.json missing")


def main():
    print(f"\n  GOLDLAB MONITOR   {datetime.now(SAST):%a %d %b %H:%M} SAST")
    print("  " + "="*66)
    feeds(); signals(); alerts(); bot(); plumbing()
    print("\n  " + "="*66)
    if problems:
        print(f"  {len(problems)} problem(s):")
        for p in problems:
            print(f"    - {p}")
    else:
        print("  everything healthy")
    print()

    if "--alert" in sys.argv and problems:
        try:
            sys.path.insert(0, HOME)
            from alerts import cfg, send
            body = ("\u26A0\uFE0F <b>Goldlab health</b>\n\n" +
                    "\n".join(f"\u2022 {p}" for p in problems) +
                    "\n\n<i>From monitor.py. Nothing else is wrong.</i>")
            send("Goldlab health", body, cfg(), urgent=True)
        except Exception as e:
            print(f"  could not send alert: {e}")


if __name__ == "__main__":
    main()
