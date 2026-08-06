#!/usr/bin/env python3
"""
alert.py - push new CRT setups to Telegram.

  python3 alert.py           send any setup not yet alerted
  python3 alert.py --test    send a test message
  python3 alert.py --daily   send the morning briefing

Config lives in ~/goldlab/logs/config.json (set it in the app under More).
Cron:  0 6 * * 1-5  cd ~/goldlab && python3 alert.py --daily
"""
import json, os, ssl, subprocess, sys, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~/goldlab")
LOGS = os.path.join(HOME, "logs")
CFG = os.path.join(LOGS, "config.json")
SENT = os.path.join(LOGS, "alerted.json")
RES = os.path.join(HOME, "results", "crt_setups.json")
SAST = timezone(timedelta(hours=2))


def cfg():
    try: return json.load(open(CFG))
    except Exception: return {}


def send(text):
    c = cfg()
    tok, chat = c.get("telegram_token"), c.get("telegram_chat")
    if not tok or not chat:
        print("  Telegram not configured. Set it in the app under More.")
        return False
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    data = urllib.parse.urlencode(
        dict(chat_id=chat, text=text, parse_mode="HTML",
             disable_web_page_preview="true")).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data),
                                    timeout=20,
                                    context=ssl.create_default_context()) as r:
            return json.loads(r.read().decode()).get("ok", False)
    except Exception as e:
        print("  send failed:", e)
        return False


def daily():
    try:
        news = subprocess.run([sys.executable, os.path.join(HOME, "newsfilter.py")],
                              capture_output=True, text=True, timeout=60).stdout
    except Exception:
        news = ""
    lines = [l.strip() for l in news.splitlines()
             if any(l.strip().startswith(s) for s in ("02:00", "08:00", "14:00"))]
    d = datetime.now(SAST)
    body = [f"<b>Heavenly Gold Lab</b> — {d:%a %d %b}", ""]
    body += ["<b>Sessions</b>"] + [f"· {l}" for l in lines] if lines else ["No session data."]
    body += ["", "Stage 1. Chart work only — nothing gets placed today."]
    return send("\n".join(body))


def new_setups():
    try: setups = json.load(open(RES))
    except Exception: return 0
    try: sent = set(json.load(open(SENT)))
    except Exception: sent = set()
    today = datetime.now(SAST).date().isoformat()
    fresh = [s for s in setups
             if (s.get("ts") or "").startswith(today)
             and f"{s.get('ts')}_{s.get('direction')}" not in sent]
    n = 0
    for s in fresh:
        aligned = s.get("bias") and ((s["bias"] == "up") == (s["direction"] == "long"))
        msg = (f"<b>CRT {s['direction'].upper()}</b>  {(s.get('ts') or '')[11:16]}\n"
               f"entry <code>{s['entry']:.2f}</code>\n"
               f"stop  <code>{s['stop']:.2f}</code>\n"
               f"target <code>{s['target']:.2f}</code>\n"
               f"R:R {s.get('rr', 0):.2f} · trend {'agrees' if aligned else 'against'}\n\n"
               f"<i>Detected, not recommended. Stage 1 — log it, do not trade it.</i>")
        if send(msg):
            sent.add(f"{s.get('ts')}_{s.get('direction')}")
            n += 1
    os.makedirs(LOGS, exist_ok=True)
    json.dump(sorted(sent), open(SENT, "w"))
    return n


if __name__ == "__main__":
    if "--test" in sys.argv:
        print("sent" if send("<b>Heavenly Gold Lab</b>\nAlerts are connected.") else "failed")
    elif "--daily" in sys.argv:
        print("sent" if daily() else "failed")
    else:
        print(f"alerted {new_setups()} setup(s)")
