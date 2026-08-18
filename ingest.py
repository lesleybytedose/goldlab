#!/usr/bin/env python3
"""
ingest.py - receives bars from MT5, writes one CSV per symbol.
Run:  python3 ~/goldlab/ingest.py    (or as a service)
Port: 127.0.0.1:8790

v2: bars carry tick volume ("v") -> written as the 8th CSV column.
    Old EAs that send no "v" still work (volume written as 0).
    /pending now refuses ALL control models, not just "faded".
"""
import json, os, re
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

RAW = os.path.expanduser("~/goldlab/data/raw")
SECRET = os.environ.get("GOLDLAB_INGEST", "changeme")
PORT = 8790
SAFE = re.compile(r"^[A-Za-z0-9_.-]{1,20}$")
HEADER = "date,time,open,high,low,close,spread,volume\n"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            d = json.loads(self.rfile.read(n).decode())
        except Exception:
            self.send_response(400); self.end_headers(); return
        if d.get("secret") != SECRET:
            self.send_response(403); self.end_headers(); return
        if d.get("fill"):
            fp = os.path.expanduser("~/goldlab/logs/demo_fills.jsonl")
            try:
                d["received"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                open(fp, "a").write(json.dumps(d["fill"] | {"at": d["received"]}) + "\n")
            except Exception:
                try: open(fp, "a").write(json.dumps(d["fill"]) + "\n")
                except Exception: pass
            self.send_response(200); self.end_headers()
            self.wfile.write(b'{"ok":true}'); return
        sym = str(d.get("symbol", ""))
        if not SAFE.match(sym):
            self.send_response(400); self.end_headers(); return
        rows = d.get("bars") or []
        if d.get("live"):
            # the candle still forming - keep it in its own file, overwritten each time
            lp = os.path.join(RAW, f"{sym}_M15_forming.json")
            try:
                json.dump(rows[0], open(lp, "w"))
            except Exception: pass
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps(dict(ok=True, live=True)).encode()); return
        os.makedirs(RAW, exist_ok=True)
        p = os.path.join(RAW, f"{sym}_M15_live.csv")
        new = not os.path.exists(p)
        seen = set()
        if not new:
            for ln in open(p):
                parts = ln.split(",")
                if len(parts) > 1:
                    seen.add(parts[0] + parts[1])
        w = 0
        with open(p, "a") as f:
            if new: f.write(HEADER)
            for b in rows:
                try:
                    key = b["d"] + b["t"]
                    if key in seen: continue
                    f.write(f"{b['d']},{b['t']},{float(b['o']):.3f},{float(b['h']):.3f},"
                            f"{float(b['l']):.3f},{float(b['c']):.3f},{float(b.get('s',0.2)):.3f},"
                            f"{int(float(b.get('v', 0)))}\n")
                    seen.add(key); w += 1
                except Exception: pass
        print(f"  {sym}: +{w} bars ({len(rows)} sent)", flush=True)
        self.send_response(200); self.end_headers()
        self.wfile.write(json.dumps(dict(ok=True, written=w)).encode())

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path); q = parse_qs(u.query)
        if not u.path.rstrip("/").endswith("pending"):
            self.send_response(200); self.end_headers()
            self.wfile.write(b"ingest up"); return
        if (q.get("secret", [""])[0]) != SECRET:
            self._json(403, dict(error="no")); return
        sym = (q.get("symbol", [""])[0])
        DEMO_MAP = {}
        sym = DEMO_MAP.get(sym, sym)
        LOG = os.path.expanduser("~/goldlab/logs/live_signals.jsonl")
        SENT = os.path.expanduser("~/goldlab/logs/sent_to_bot.json")
        try:
            sent = set(json.load(open(SENT)))
        except Exception:
            sent = set()
        out = []
        if os.path.exists(LOG):
            import time as _t
            cutoff = _t.time() - 3600          # only signals from the last hour
            for ln in open(LOG):
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("phase") == "backfill": continue
                if sym and r.get("sym") != sym: continue
                if r.get("k") in sent: continue
                # controls and mirrors are for measurement, never for orders
                NO_TRADE = ("faded", "RANDOM", "RNDLVL")
                if any(x in r.get("model", "") for x in NO_TRADE): continue
                try:
                    ts = datetime.fromisoformat(r["ts"]).timestamp()
                except Exception:
                    continue
                if ts < cutoff: continue
                out.append(dict(k=r["k"], model=r["model"], dir=r["dir"],
                                entry=r["entry"], stop=r["stop"],
                                target=r["target"], ts=r["ts"]))
        out = out[:5]
        if out:
            sent |= {o["k"] for o in out}
            try: json.dump(sorted(sent)[-5000:], open(SENT, "w"))
            except Exception: pass
        self._json(200, dict(signals=out))


if __name__ == "__main__":
    if SECRET == "changeme":
        print("\n  Set a secret first:\n    export GOLDLAB_INGEST='something-long'\n")
    print(f"  ingest listening on 127.0.0.1:{PORT}")
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
