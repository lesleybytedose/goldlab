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

try:
    from advice import quality_block
except Exception:
    quality_block = lambda sig, html=False: ""
try:
    from alert_extras import usd_lines, confluence, urgent_header
except Exception:
    usd_lines = lambda sig, html=False: ""
    confluence = lambda sig: (0, [], False)
    urgent_header = lambda n, names, html=False: ""

FEED_TZ = timezone.utc
SAST = timezone(timedelta(hours=2))
HOME = os.path.expanduser("~/goldlab")
LOGS = os.path.join(HOME, "logs")
CFG = os.path.join(HOME, "alerts.json")
SEEN = os.path.join(LOGS, "alerts_seen.json")

# what to send. Turn things off here if it gets noisy.
SEND = dict(signals=True, fills=True, feed=True, news=True, approach=True, forming=True)
NEWS_LEAD = 20          # minutes before an event
QUIET_START, QUIET_END = None, None   # e.g. 23, 6 to mute overnight


GOLD_HEADER = "\U0001F3C5 <b>HEAVENLY GOLD LAB</b>"
SYN_HEADER  = "\u26A1 <b>HEAVENLY LAB \u00b7 SYNTHETICS</b>"
HEADER = GOLD_HEADER
RULE   = "\u2501" * 17
FOOTER = "<i>Designed by Lesley N \U0001F981 \u00b7 Heavenly Guard \U0001F60E</i>"


def levels(e, sp, tg):
    risk = abs(e - sp)
    if risk <= 0: return []
    sgn = 1 if tg > e else -1
    rr = abs(tg - e) / risk
    t1 = e + sgn * (risk if rr > 1.2 else abs(tg - e) * 0.5)
    t2 = (t1 + tg) / 2.0
    return [("TP1", t1, 50), ("TP2", t2, 25), ("TP3", tg, 25)]


def wrap(body, sym=None):
    head = SYN_HEADER if (sym and not str(sym).startswith("XAU")) else GOLD_HEADER
    """Every alert gets the same frame so they read consistently."""
    return f"{head}\n{RULE}\n\n{body}\n\n{RULE}\n{FOOTER}"


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


def whatsapp(msg, c):
    """CallMeBot - free, one phone, no Meta account needed."""
    people = []
    if c.get("wa_phone") and c.get("wa_key"):
        people.append((c["wa_phone"], c["wa_key"]))
    for p in c.get("wa_extra", []):
        if p.get("phone") and p.get("key"):
            people.append((p["phone"], p["key"]))
    if not people:
        return False
    txt = (msg.replace("<b>", "*").replace("</b>", "*")
              .replace("<i>", "_").replace("</i>", "_")
              .replace("<code>", "").replace("</code>", ""))
    sent = 0
    for phone, key in people:
        url = ("https://api.callmebot.com/whatsapp.php?phone="
               + urllib.parse.quote(str(phone))
               + "&text=" + urllib.parse.quote(txt)
               + "&apikey=" + urllib.parse.quote(str(key)))
        try:
            rr = urllib.request.urlopen(url, timeout=20,
                                   context=ssl.create_default_context())
            body = rr.read(4000).decode("utf-8", "ignore")
            low = body.lower()
            if not ("error" in low or "not allowed" in low or "limit" in low):
                sent += 1
            else:
                print("  wa reject:", body[:160].replace(chr(10), " "))
        except Exception as e:
            print(f"  whatsapp to {phone} failed:", e)
    return sent > 0


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


def send(subject, body, c, urgent=False, sym=None):
    if quiet_now() and not urgent:
        print("  quiet hours, held back")
        return
    body = wrap(body, sym)
    ok = telegram(body, c)
    ok = whatsapp(body, c) or ok
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


NICE = {
    "XAUUSDm":             ("\U0001F947", "Gold"),
    "XAUUSD247m":          ("\U0001F947", "Gold 24/7"),
    "XAUUSDc":             ("\U0001F947", "Gold"),
    "XAGUSD":              ("\U0001F948", "Silver"),
    "Volatility_75_Index": ("\U0001F4C8", "V75"),
    "Crash_1000_Index":    ("\U0001F4A5", "Crash 1000"),
    "Boom_1000_Index":     ("\U0001F680", "Boom 1000"),
    "EURUSDc":             ("\U0001F4B6", "EURUSD"),
    "EURGBPc":             ("\U0001F4B7", "EURGBP"),
}


MICON = {
    "CRT sweep":          "\U0001F30A",
    "Order block":        "\U0001F9F1",
    "London break":       "\U0001F3DB",
    "Range spike":        "\u26A1",
    "Contraction break":  "\U0001F5DC",
    "M1 daily extreme":   "\U0001F4CD",
    "M1 faded":           "\U0001F503",
    "RANDOM CONTROL":     "\U0001F3B2",
    "MA/ATR band":        "\U0001F4CF",
    "Donchian SAR":       "\U0001F4E1",
    "Donchian SAR p10":   "\U0001F4E1",
    "Failure test":       "\U0001FA9D",
    "Break-retest pivot": "\U0001F9F2",
    "Contraction v2 b4t1.6": "\U0001F5DC",
}


def next_news(win=45):
    p = os.path.join(LOGS, "news_cache.json")
    try:
        ev = json.load(open(p)).get("events") or []
    except Exception:
        return None
    up = [e for e in ev if 0 <= (e.get("mins") or 9e9) <= win
          and str(e.get("impact","")).lower() in ("high","medium")]
    return sorted(up, key=lambda e: e["mins"])[0] if up else None


def model_record(m):
    st = state()
    for row in (st.get("models") or []):
        if row.get("name") == m:
            return row
    return None


def mtag(m):
    return MICON.get(str(m), "\U0001F4CB") + " " + str(m)


def nice(sym):
    ic, nm = NICE.get(str(sym), ("\U0001F4CA", str(sym)))
    return ic + " <b>" + nm + "</b>"


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
    # 247m mirrors the main gold symbol, and the faded models are controls,
    # not tradeable calls. Both still get logged and scored - just not alerted.
    SKIP_ALERT = ("faded", "RANDOM CONTROL", "RANDOM CTRL", "RNDLVL")
    seen_event = set()
    sigs = load_jsonl("live_signals.jsonl")[-400:]
    for r in sigs:
        if any(x in r.get("model", "") for x in SKIP_ALERT):
            continue
        ev = (r.get("model"), r.get("dir"), r.get("ts"),
              r.get("sym", "").replace("247", ""))
        if ev in seen_event:
            continue
        seen_event.add(ev)
        if r.get("phase") == "backfill":
            continue
        k = "sig:" + str(r.get("k"))
        if k in known:
            continue
        try:
            ts = datetime.fromisoformat(r["ts"]).replace(tzinfo=FEED_TZ)
        except Exception:
            continue
        if (now - ts).total_seconds() > 1800:
            fresh.append(k)          # too old to shout about, but mark it
            continue
        d = (r.get("dir") or "").upper()
        arrow = "▲" if d == "LONG" else "▼"
        e = float(r.get("entry") or 0)
        sp = float(r.get("stop") or 0)
        tg = float(r.get("target") or 0)
        risk = abs(e - sp)
        L = [f"{arrow} <b>{d}</b>   {nice(r.get('sym'))}",
             f"{mtag(r.get('model'))}", "",
             "<code>",
             f"entry   {e:,.2f}",
             f"stop    {sp:,.2f}   {-risk:+,.2f}"]
        for nm, px, pct in levels(e, sp, tg):
            rm = abs(px - e) / risk if risk else 0
            L.append(f"{nm}     {px:,.2f}   {rm:.1f}R   {pct}%")
        sprd = float(r.get("spread") or 0)
        if sprd > 0 and risk > 0:
            L.append(f"spread  {sprd:g}   ({sprd/risk*100:.0f}% of risk)")
        L.append("</code>")
        ux = usd_lines(r, html=True)
        if ux:
            L.append(ux.strip())
        opp = [o for o in sigs
               if o.get("sym") == r.get("sym") and o.get("ts") == r.get("ts")
               and o.get("dir") != r.get("dir")]
        if opp:
            L.append("")
            L.append("\U0001F500 <b>conflict</b>: "
                     + ", ".join(f"{o['model']} {o['dir'].upper()}"
                                 for o in opp[:3])
                     + " on the same candle")
        ne = next_news()
        if ne:
            L.append(f"\u26A0\uFE0F {ne['title']} in {ne['mins']} min")
        rec = model_record(r.get("model"))
        ctl = model_record("RANDOM CONTROL")
        if rec and rec.get("win") is not None:
            L.append("")
            L.append(f"{rec['win']*100:.0f}% hit \u00b7 "
                     f"{rec.get('exp',0):+.2f}R avg \u00b7 "
                     f"{rec.get('n',0):,} trades")
            if ctl and ctl.get("win") is not None:
                L.append(f"\U0001F3B2 control: {ctl['win']*100:.0f}% \u00b7 "
                         f"{ctl.get('exp',0):+.2f}R")
        qb = quality_block(r, html=True).strip()
        if qb:
            L.append("")
            L.append(qb)
        L.append("")
        L.append(f"candle {ts.astimezone(SAST):%H:%M} closed "
                 f"{(ts+timedelta(minutes=15)).astimezone(SAST):%H:%M} sent "
                 f"{datetime.now(SAST):%H:%M} SAST")
        n_ag, ag_names, urgent_flag = confluence(r)
        if urgent_flag:
            L.insert(0, urgent_header(n_ag, ag_names, html=True))
        msg = chr(10).join(L)
        send(("URGENT " if urgent_flag else "") + f"Signal: {r.get('model')} {d}",
             msg, c, urgent=urgent_flag, sym=r.get("sym"))
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


def check_forming(c, known, fresh):
    """Every model, against the candle still open. Reads forming.py output."""
    p = os.path.join(LOGS, "forming.json")
    if not os.path.exists(p):
        return
    try:
        d = json.load(open(p))
    except Exception:
        return
    rows = d.get("forming") or []
    left = d.get("minutes_left", 0)
    marker = os.path.join(LOGS, "forming_last.txt")
    prev = open(marker).read().strip() if os.path.exists(marker) else ""
    nowkey = "|".join(sorted(r["model"] + r["sym"] + r["dir"] for r in rows))
    try:
        open(marker, "w").write(nowkey)
    except Exception:
        pass

    if not rows:
        if prev:
            gone = []
            for part in prev.split("|"):
                if part.strip():
                    gone.append("\u2022 " + part)
            detail = (chr(10).join(gone) if gone else "")
            send("Forming gone",
                 "\u274c <b>Setup gone</b>\n\n" + detail +
                 "\n\nWhat was forming has dropped away. "
                 "Nothing would fire if this candle closed now.", c)
        return
    if left < 2:
        return
    stamp = rows[0].get("ts", "")
    k = "form:" + stamp + ("|late" if left <= 3 else "")
    if k in known:
        return
    lines = []
    for r in rows[:6]:
        arrow = "\U0001F7E2" if r["dir"] == "long" else "\U0001F534"
        rec = model_record(r["model"])
        hit = f"  ({rec['win']*100:.0f}% hit)" if rec and rec.get("win") is not None else ""
        lines.append(f"{arrow} <b>{r['dir'].upper()}</b>   "
                     f"{nice(r['sym'])}\n"
                     f"{mtag(r['model'])}{hit}\n"
                     f"entry {r['entry']}   stop {r['stop']}")
    more = f"\n\n+{len(rows)-6} more" if len(rows) > 6 else ""
    head = ("\U0001F6A8 <b>Closing soon — still forming</b>" if left <= 5
            else "\U0001F440 <b>Forming now — not confirmed</b>")
    msg = (head + "\n\n" + "\n\n".join(lines) + more +
           f"\n\n\u23f3 Candle closes in about {left} minutes "
           f"({rows[0].get('closes','')} SAST).\n\n"
           "<i>Nothing is confirmed until the candle closes. Have the chart "
           "open. If it still looks like this at the close, the signal "
           "will follow with entry, stop and targets.</i>")
    send("Forming", msg, c, urgent=True)
    fresh.append(k)


def check_approach(c, known, fresh):
    if not SEND.get("approach"):
        return
    """Price closing in on a level that would trigger something. Earliest warning."""
    p = os.path.join(LOGS, "forming.json")
    if not os.path.exists(p):
        return
    try:
        d = json.load(open(p))
    except Exception:
        return
    # round numbers alone are noise - only warn on levels a model measures against
    MEANINGFUL = ("opening range", "yesterday", "recent", "week", "today", "round number")
    rows = [r for r in (d.get("approaching") or [])
            if r.get("dist_atr", 9) <= 0.4
            and any(m in r.get("what", "") for m in MEANINGFUL)]
    if not rows:
        return
    # one per level per hour, or it repeats endlessly as price hovers
    hour = datetime.now(SAST).strftime("%H")
    fired = []
    for r in rows[:4]:
        k = f"near:{hour}:{r['sym']}:{r['level']}:{r['side']}"
        if k in known:
            continue
        fired.append((k, r))
    if not fired:
        return
    lines = [f"\u2022 <b>{r['sym']}</b> is {r['dist_price']} from the "
             f"{r['what']} {r['side']} at {r['level']}"
             for _, r in fired]
    msg = ("\U0001F9ED <b>Price approaching a level</b>\n\n"
           + "\n".join(lines) +
           "\n\n<i>Nothing has happened yet. If price sweeps this level and "
           "closes back, a setup forms. Worth having the chart open.</i>")
    send("Approaching", msg, c)
    fresh.extend(k for k, _ in fired)


def check_feed(c, known, fresh):
    if not SEND["feed"]:
        return
    s = state()
    # symbols no longer fed since MT5 moved to the demo account
    IGNORE_FEEDS = ("EURUSDc", "EURGBPc", "XAUUSDc")
    # spot gold settles daily ~20:45-22:00 UTC and closes at the weekend.
    # the 247 symbol trades through both, so it stays monitored.
    from datetime import datetime as _dt
    u = _dt.now(FEED_TZ)
    settlement = u.hour in (20, 21)
    weekend = u.weekday() == 5 or (u.weekday() == 4 and u.hour >= 21) \
              or (u.weekday() == 6 and u.hour < 22)
    quiet = settlement or weekend
    down = [f["symbol"] for f in s.get("feeds", [])
            if not (quiet and "247" not in f["symbol"])
            and f.get("stale") and f["symbol"] not in IGNORE_FEEDS]
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
    check_forming(c, known, fresh)
    check_approach(c, known, fresh)
    check_feed(c, known, fresh)
    check_signals(c, known, fresh)
    check_fills(c, known, fresh)
    check_news(c, known, fresh)
    if fresh:
        remember(fresh)
    print(f"  {datetime.now(SAST):%H:%M}  {len(fresh)} new item(s)")


if __name__ == "__main__":
    main()
