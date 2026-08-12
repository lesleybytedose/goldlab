#!/usr/bin/env python3
"""
alerts.py - tells you when something happens. Nothing else.

Sends on:
  new signal fired          (a model just found a setup)
  demo trade filled         (the bot executed, with slippage)
  feed down / back up       (data stopped arriving)
  high-impact news in 20m   (once per event)

Never repeats itself. Runs from cron every 5 minutes.

  python3 alerts.py            check and send
  python3 alerts.py --test     send one test message
  python3 alerts.py --setup    show how to configure
"""
import json, os, ssl, smtplib, sys, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage

FEED_TZ = timezone.utc
SAST = timezone(timedelta(hours=2))
HOME = os.path.expanduser("~/goldlab")
LOGS = os.path.join(HOME, "logs")
CFG = os.path.join(HOME, "alerts.json")
SEEN = os.path.join(LOGS, "alerts_seen.json")

# what to send. Turn things off here if it gets noisy.
SEND = dict(signals=True, fills=True, feed=True, news=True)
NEWS_LEAD = 20          # minutes before an event
QUIET_START, QUIET_END = None, None   # e.g. 23, 6 to mute overnight


def cfg():
    if not os.path.exists(CFG):
        return {}
    try:
        return json.load(open(CFG))
    except Exception:
        return {}


def seen():
    try:
        return set(json.load(open(SEEN)))
    except Exception:
        return set()


def remember(keys):
    s = seen() | set(keys)
    try:
        json.dump(sorted(s)[-4000:], open(SEEN, "w"))
    except Exception:
        pass


def quiet_now():
    if QUIET_START is None:
        return False
    h = datetime.now(SAST).hour
    if QUIET_START < QUIET_END:
        return QUIET_START <= h < QUIET_END
    return h >= QUIET_START or h < QUIET_END


def telegram(msg, c):
    tok, chat = c.get("telegram_token"), c.get("telegram_chat")
    if not tok or not chat:
        return False
    url = f"https://api.telegram.org/bot{tok}/sendMessage"
    data = urllib.parse.urlencode(dict(
        chat_id=chat, text=msg, parse_mode="HTML",
        disable_web_page_preview="true")).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data),
                               timeout=15, context=ssl.create_default_context())
        return True
    except Exception as e:
        print("  telegram failed:", e)
        return False


def email(subject, body, c):
    host, user, pwd = c.get("smtp_host"), c.get("smtp_user"), c.get("smtp_pass")
    to = c.get("email_to")
    if not (host and user and pwd and to):
        return False
    m = EmailMessage()
    m["Subject"] = subject
    m["From"] = user
    m["To"] = to
    m.set_content(body)
    try:
        port = int(c.get("smtp_port", 587))
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(user, pwd)
            s.send_message(m)
        return True
    except Exception as e:
        print("  email failed:", e)
        return False


def send(subject, body, c, urgent=False):
    if quiet_now() and not urgent:
        print("  quiet hours, held back")
        return
    ok = telegram(body, c)
    if c.get("email_always") or not ok:
        email(subject, body.replace("<b>", "").replace("</b>", ""), c)
    print("  sent:", subject)


def load_jsonl(name):
    p = os.path.join(LOGS, name)
    out = []
    if os.path.exists(p):
        for ln in open(p):
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def state():
    p = os.path.join(HOME, "web", "state.json")
    try:
        return json.load(open(p))
    except Exception:
        return {}


def check_signals(c, known, fresh):
    if not SEND["signals"]:
        return
    now = datetime.now(FEED_TZ)
    for r in load_jsonl("live_signals.jsonl")[-400:]:
        if r.get("phase") == "backfill":
            continue
        k = "sig:" + str(r.get("k"))
        if k in known:
            continue
        try:
            ts = datetime.fromisoformat(r["ts"]).replace(tzinfo=FEED_TZ)
        except Exception:
            continue
        if (now - ts).total_seconds() > 3600:
            fresh.append(k)          # too old to shout about, but mark it
            continue
        d = (r.get("dir") or "").upper()
        arrow = "▲" if d == "LONG" else "▼"
        msg = (f"{arrow} <b>{r.get('model')}</b>  {d}  {r.get('sym')}\n"
               f"entry {r.get('entry')}   stop {r.get('stop')}   target {r.get('target')}\n"
               f"{ts.astimezone(SAST):%H:%M} SAST")
        send(f"Signal: {r.get('model')} {d}", msg, c)
        fresh.append(k)


def check_fills(c, known, fresh):
    if not SEND["fills"]:
        return
    for r in load_jsonl("demo_fills.jsonl")[-100:]:
        k = "fill:" + str(r.get("k"))
        if k in known:
            continue
        slip = None
        try:
            slip = round(float(r["fill"]) - float(r["signal"]), 3)
        except Exception:
            pass
        ok = r.get("ok")
        msg = (f"{'✅' if ok else '❌'} <b>Demo {'filled' if ok else 'rejected'}</b>  "
               f"{r.get('model')} {(r.get('dir') or '').upper()}\n"
               f"{r.get('lots')} lots on {r.get('sym')}\n"
               f"signal {r.get('signal')}  →  fill {r.get('fill')}"
               + (f"   slip {slip:+}" if slip is not None else "")
               + f"\nbalance {r.get('balance')}")
        send("Demo fill", msg, c)
        fresh.append(k)


def check_feed(c, known, fresh):
    if not SEND["feed"]:
        return
    s = state()
    down = [f["symbol"] for f in s.get("feeds", []) if f.get("stale")]
    marker = os.path.join(LOGS, "alerts_feed.state")
    prev = ""
    if os.path.exists(marker):
        prev = open(marker).read().strip()
    now_state = ",".join(sorted(down))
    if now_state != prev:
        if down:
            send("Feed down",
                 f"⚠️ <b>Feed stopped</b>\n{', '.join(down)}\n"
                 f"MT5 may have closed on the VPS.", c, urgent=True)
        elif prev:
            send("Feed back", "✅ <b>Feed back up</b>", c)
        try:
            open(marker, "w").write(now_state)
        except Exception:
            pass


def check_news(c, known, fresh):
    if not SEND["news"]:
        return
    for n in state().get("news", []):
        if not (0 < n.get("mins", 999) <= NEWS_LEAD):
            continue
        k = "news:" + n.get("title", "") + n.get("sast", "")
        if k in known:
            continue
        msg = (f"📅 <b>{n.get('title')}</b>\n"
               f"{n.get('cur')} · {n.get('impact')} impact\n"
               f"in {n.get('mins')} minutes ({n.get('sast')} SAST)\n"
               f"Spreads usually widen around this.")
        send("News incoming", msg, c, urgent=(n.get("impact") == "High"))
        fresh.append(k)


SETUP = """
  Create ~/goldlab/alerts.json with whichever of these you want:

  {
    "telegram_token": "from @BotFather after /newbot",
    "telegram_chat":  "your chat id",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "you@gmail.com",
    "smtp_pass": "a Google app password, not your login",
    "email_to":  "you@gmail.com",
    "email_always": false
  }

  Telegram in three steps:
    1. Message @BotFather, send /newbot, follow it, copy the token
    2. Message your new bot anything
    3. Open api.telegram.org/bot<TOKEN>/getUpdates and copy the chat id

  Then:  chmod 600 ~/goldlab/alerts.json
         python3 alerts.py --test

  Telegram is used first. Email is the fallback if Telegram fails,
  or always if you set email_always to true.
"""


def main():
    c = cfg()
    if "--setup" in sys.argv:
        print(SETUP); return
    if not c:
        print("  no alerts.json yet — run: python3 alerts.py --setup"); return
    if "--test" in sys.argv:
        send("Goldlab test",
             "🔔 <b>Goldlab alerts are working.</b>\n"
             "You will get a message when a model fires, when the demo bot "
             "fills a trade, when the feed stops, and before high-impact news.", c)
        return
    known = seen()
    fresh = []
    check_feed(c, known, fresh)
    check_signals(c, known, fresh)
    check_fills(c, known, fresh)
    check_news(c, known, fresh)
    if fresh:
        remember(fresh)
    print(f"  {datetime.now(SAST):%H:%M}  {len(fresh)} new item(s)")


if __name__ == "__main__":
    main()
