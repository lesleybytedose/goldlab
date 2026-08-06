#!/usr/bin/env python3
"""
api.py - Heavenly Gold Lab API.

Run:  python3 ~/goldlab/api.py
Bind: 127.0.0.1:8788   (nginx proxies /goldlab/api/ here)
"""
import csv, json, os, ssl, subprocess, sys, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

HOME = os.path.expanduser("~/goldlab")
LOGS = os.path.join(HOME, "logs")
RAW = os.path.join(HOME, "data", "raw")
RESULTS = os.path.join(HOME, "results")
OBS = os.path.join(LOGS, "observations.jsonl")
TRADES = os.path.join(LOGS, "trades.jsonl")
CFG = os.path.join(LOGS, "config.json")
PROG = os.path.join(LOGS, "progress.json")
SAST = timezone(timedelta(hours=2))
PORT = 8788

DEFAULTS = dict(balance=46.87, risk_pct=1.0, contract_oz=1.0, min_lot=0.01,
                spread=0.27, max_trades=3, max_daily_loss=3.0,
                telegram_token="", telegram_chat="")

STAGES = [
    dict(n=1, name="Observe", target=30, rule="Mark setups. Log them. Place nothing.",
         allows="Chart work only", live=False),
    dict(n=2, name="Paper", target=60, rule="Same setups, paper-traded, full discipline.",
         allows="Simulated entries", live=False),
    dict(n=3, name="Minimum risk", target=100, rule="Live cent account. 0.5% risk. One a day.",
         allows="One live trade per session", live=True),
    dict(n=4, name="Standard", target=100, rule="Live. 1% risk. Three trades a day maximum.",
         allows="Full system", live=True),
]


def jsonl(p):
    out = []
    if os.path.exists(p):
        for ln in open(p):
            try: out.append(json.loads(ln))
            except Exception: pass
    return out


def cfg():
    c = dict(DEFAULTS)
    try: c.update(json.load(open(CFG)))
    except Exception: pass
    return c


def save_cfg(d):
    c = cfg(); c.update({k: v for k, v in d.items() if k in DEFAULTS})
    os.makedirs(LOGS, exist_ok=True)
    json.dump(c, open(CFG, "w"), indent=2)
    return c


def bars(n=400):
    for name in ("XAUUSD_M15_dukas.csv", "XAUUSD_M15.csv"):
        p = os.path.join(RAW, name)
        if not os.path.exists(p): continue
        rows = []
        for r in csv.DictReader(open(p)):
            try:
                rows.append(dict(t=f"{r['date']} {r['time']}", o=float(r["open"]),
                                 h=float(r["high"]), l=float(r["low"]), c=float(r["close"])))
            except Exception: pass
        return rows[-n:], name
    return [], None


def setups():
    p = os.path.join(RESULTS, "crt_setups.json")
    try: d = json.load(open(p))
    except Exception: return []
    d.sort(key=lambda s: s.get("ts", ""))
    return d


def news():
    try:
        out = subprocess.run([sys.executable, os.path.join(HOME, "newsfilter.py")],
                             capture_output=True, text=True, timeout=45).stdout
    except Exception:
        return [], []
    verdicts, events = [], []
    for ln in out.splitlines():
        s = ln.strip()
        for lab in ("02:00", "08:00", "14:00"):
            if s.startswith(lab) and ":" in s and "-" in s:
                v = "green"
                for k, n in (("RED", "red"), ("AMBER", "amber"), ("YELLOW", "yellow")):
                    if k in s: v = n
                verdicts.append(dict(session=lab, verdict=v, passed="(passed)" in s,
                                     text=s.split(":", 1)[-1].strip()))
        if any(m in s for m in ("!!!", " !!", " ~ ", "  .")) and ":" in s:
            tier = "tier1" if "!!!" in s else "high" if "!!" in s else \
                   "speaker" if "~" in s else "medium"
            txt = s.replace("!!!", "").replace("!!", "").replace("~", "").strip(" .")
            events.append(dict(tier=tier, text=txt))
    return verdicts, events


def agg(rows):
    d = [r for r in rows if r.get("result") is not None]
    if not d: return None
    R = [float(r["result"]) for r in d]
    w = [x for x in R if x > 0]; l = [x for x in R if x <= 0]
    exp = sum(R) / len(R)
    return dict(n=len(R), win=len(w) / len(R), exp=exp, total=sum(R),
                pf=(sum(w) / abs(sum(l))) if l else None,
                avg_win=(sum(w) / len(w)) if w else 0,
                avg_loss=(sum(l) / len(l)) if l else 0,
                best=max(R), worst=min(R))


def equity(rows):
    d = [r for r in rows if r.get("result") is not None]
    cum, out, peak, dd = 0.0, [], 0.0, 0.0
    for r in d:
        cum += float(r["result"])
        peak = max(peak, cum); dd = min(dd, cum - peak)
        out.append(dict(t=(r.get("ts") or "")[:16].replace("T", " "), r=round(cum, 3)))
    return out, round(dd, 2)


def distribution(rows):
    buckets = {}
    for r in rows:
        if r.get("result") is None: continue
        v = float(r["result"])
        k = max(-3, min(3, int(round(v))))
        buckets[k] = buckets.get(k, 0) + 1
    return [dict(r=k, n=buckets.get(k, 0)) for k in range(-3, 4)]


def by_hour(rows):
    h = {}
    for r in rows:
        if r.get("result") is None: continue
        try: hh = int((r.get("ts") or "")[11:13])
        except Exception: continue
        h.setdefault(hh, []).append(float(r["result"]))
    return [dict(hour=k, n=len(v), exp=round(sum(v) / len(v), 3))
            for k, v in sorted(h.items())]


def slices(rows):
    def bias_ok(s):
        return s.get("bias") and ((s["bias"] == "up") == (s["direction"] == "long"))
    defs = [
        ("Trend agrees", lambda s: bias_ok(s)),
        ("Trend against", lambda s: s.get("bias") and not bias_ok(s)),
        ("Short", lambda s: s["direction"] == "short"),
        ("Long", lambda s: s["direction"] == "long"),
        ("Session 02:00", lambda s: s.get("session") == "02:00"),
        ("Session 08:00", lambda s: s.get("session") == "08:00"),
        ("Session 14:00", lambda s: s.get("session") == "14:00"),
        ("Outside sessions", lambda s: s.get("session") == "off"),
        ("R:R above 2", lambda s: float(s.get("rr", 0)) >= 2),
        ("Trend + session", lambda s: bias_ok(s) and s.get("session") != "off"),
    ]
    out = []
    for name, fn in defs:
        a = agg([s for s in rows if fn(s)])
        if a: out.append(dict(name=name, **a))
    return out


def size(entry, stop, balance, risk_pct, c):
    dist = abs(entry - stop)
    if dist <= 0: return dict(error="Stop equals entry")
    risk_money = balance * risk_pct / 100.0
    raw = risk_money / (dist * c["contract_oz"])
    lots = round(raw, 2)
    ok = raw >= c["min_lot"]
    actual = max(lots, c["min_lot"]) * dist * c["contract_oz"]
    return dict(stop_dist=round(dist, 2), risk_money=round(risk_money, 2),
                raw_lots=round(raw, 4), lots=lots, tradeable=ok,
                min_lot_risk=round(c["min_lot"] * dist * c["contract_oz"], 2),
                min_lot_pct=round(c["min_lot"] * dist * c["contract_oz"] / balance * 100, 1),
                needed_balance=round(c["min_lot"] * dist * c["contract_oz"] / (risk_pct / 100), 2),
                spread_cost=round(c["spread"] * max(lots, c["min_lot"]) * c["contract_oz"], 2))


def telegram(text, c):
    if not c.get("telegram_token") or not c.get("telegram_chat"):
        return dict(ok=False, error="Telegram not configured")
    url = f"https://api.telegram.org/bot{c['telegram_token']}/sendMessage"
    body = urllib.parse.urlencode(dict(chat_id=c["telegram_chat"], text=text,
                                       parse_mode="HTML")).encode()
    try:
        req = urllib.request.Request(url, data=body)
        with urllib.request.urlopen(req, timeout=20,
                                    context=ssl.create_default_context()) as r:
            return dict(ok=json.loads(r.read().decode()).get("ok", False))
    except Exception as e:
        return dict(ok=False, error=str(e))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _s(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(b)

    def _p(self):
        p = urllib.parse.urlparse(self.path).path.rstrip("/")
        return p.replace("/goldlab/api", "").replace("/api", "") or "/"

    def do_GET(self):
        p, c = self._p(), cfg()
        try:
            if p in ("/", "/status"):
                obs = jsonl(OBS)
                try: st = json.load(open(PROG)).get("stage", 1)
                except Exception: st = 1
                v, e = news()
                return self._s(dict(stage=STAGES[max(0, min(3, st - 1))],
                                    observations=len(obs), trades=len(jsonl(TRADES)),
                                    news=v, events=e, config=c,
                                    now=datetime.now(SAST).isoformat()))
            if p == "/setups": return self._s(setups()[-400:])
            if p == "/bars":
                b, s = bars(); return self._s(dict(bars=b, source=s))
            if p == "/observations": return self._s(jsonl(OBS))
            if p == "/trades": return self._s(jsonl(TRADES))
            if p == "/config": return self._s(c)
            if p == "/stats":
                S = setups()
                eq, dd = equity(S)
                a = agg(S) or {}
                money = None
                if a:
                    rm = c["balance"] * c["risk_pct"] / 100.0
                    money = dict(per_trade=round(a.get("exp", 0) * rm, 2),
                                 total=round(a.get("total", 0) * rm, 2),
                                 risk_per_trade=round(rm, 2))
                return self._s(dict(all=a, slices=slices(S), equity=eq, drawdown=dd,
                                    dist=distribution(S), hours=by_hour(S), money=money))
            self._s({"error": "not found"}, 404)
        except Exception as e:
            self._s({"error": str(e)}, 500)

    def do_POST(self):
        p = self._p()
        n = int(self.headers.get("Content-Length", 0))
        try: d = json.loads(self.rfile.read(n).decode())
        except Exception: return self._s({"error": "bad json"}, 400)
        c = cfg()
        try:
            if p == "/observe":
                d.update(ts=datetime.now(SAST).isoformat(), source="human")
                os.makedirs(LOGS, exist_ok=True)
                open(OBS, "a").write(json.dumps(d) + "\n")
                return self._s(dict(ok=True, total=len(jsonl(OBS))))
            if p == "/trade":
                d.update(ts=datetime.now(SAST).isoformat())
                os.makedirs(LOGS, exist_ok=True)
                open(TRADES, "a").write(json.dumps(d) + "\n")
                return self._s(dict(ok=True, total=len(jsonl(TRADES))))
            if p == "/size":
                return self._s(size(float(d["entry"]), float(d["stop"]),
                                    float(d.get("balance") or c["balance"]),
                                    float(d.get("risk_pct") or c["risk_pct"]), c))
            if p == "/config":
                return self._s(save_cfg(d))
            if p == "/telegram":
                return self._s(telegram(d.get("text", "Heavenly Gold Lab test"), c))
            self._s({"error": "not found"}, 404)
        except Exception as e:
            self._s({"error": str(e)}, 500)


if __name__ == "__main__":
    print(f"  Heavenly Gold Lab API on 127.0.0.1:{PORT}")
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
