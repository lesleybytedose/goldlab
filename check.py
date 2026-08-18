#!/usr/bin/env python3
"""
check.py - does GoldLab actually do what it claims?

Read-only audit. Writes nothing, sends nothing, changes nothing.
Every line is a promise the system makes, checked against the files
on disk. Run it any time:  python3 ~/goldlab/check.py
"""
import glob, hashlib, json, os, subprocess, sys
from datetime import datetime, timezone, timedelta

HOME = os.path.expanduser("~/goldlab")
RAW = os.path.join(HOME, "data/raw")
LOGS = os.path.join(HOME, "logs")
UTC = timezone.utc
SAST = timezone(timedelta(hours=2))

PASS, FAIL, WARN = [], [], []
def ok(m):   PASS.append(m); print(f"  \033[32mPASS\033[0m  {m}")
def bad(m):  FAIL.append(m); print(f"  \033[31mFAIL\033[0m  {m}")
def warn(m): WARN.append(m); print(f"  \033[33mWARN\033[0m  {m}")
def head(m): print(f"\n  {m}\n  " + "-"*70)


def load_signals():
    p = os.path.join(LOGS, "live_signals.jsonl")
    rows = []
    if os.path.exists(p):
        for ln in open(p):
            try: rows.append(json.loads(ln))
            except Exception: pass
    return rows


def state():
    for p in ("/opt/hgs/client/goldlab/state.json", os.path.join(HOME,"web","state.json")):
        try: return json.load(open(p))
        except Exception: pass
    return {}


# ---------------------------------------------------------------- 1. code
head("1. CODE — are the intended versions actually installed?")
want = {"live.py": ("covs(", "held"), "publish.py": ("evaluation", "is_holdout", "_welch_p"),
        "ingest.py": ("volume", "RNDLVL"), "alerts.py": ("quality_block", "confluence"),
        "advice.py": ("quality_block",), "alert_extras.py": ("_mult_lines", "MULT"),
        "book_models_live.py": ("bk_failure_test", "bk_random_20")}
for f, marks in want.items():
    p = os.path.join(HOME, f)
    if not os.path.exists(p):
        bad(f"{f} missing"); continue
    src = open(p).read()
    miss = [m for m in marks if m not in src]
    if miss: bad(f"{f} present but stale — missing: {', '.join(miss)}")
    else: ok(f"{f} current ({len(src.splitlines())} lines)")

try:
    sys.path.insert(0, HOME)
    import live as _live
    n = len(_live.MODELS)
    ctrl = [m for m in _live.MODELS if "RANDOM" in m or "RNDLVL" in m]
    (ok if n >= 17 else warn)(f"{n} models registered, {len(ctrl)} of them controls")
except Exception as e:
    bad(f"live.py will not import: {e}")


# ---------------------------------------------------------------- 2. feeds
head("2. FEEDS — is data arriving, and does it carry volume?")
now = datetime.now(UTC)
for p in sorted(glob.glob(os.path.join(RAW, "*_M15_live.csv"))):
    sym = os.path.basename(p).replace("_M15_live.csv","")
    lines = open(p).read().splitlines()
    if len(lines) < 2: warn(f"{sym}: empty"); continue
    hdr, last = lines[0], lines[-1]
    volcol = hdr.strip().endswith(",volume")
    try:
        t = datetime.fromisoformat(last.split(",")[0] + " " + last.split(",")[1]).replace(tzinfo=UTC)
        age = (now - t).total_seconds()/60
    except Exception:
        age = None
    vols = []
    for ln in lines[-40:]:
        parts = ln.split(",")
        if len(parts) >= 8:
            try: vols.append(float(parts[7]))
            except Exception: pass
    live_vol = sum(1 for v in vols if v > 0)
    msg = f"{sym}: {len(lines)-1} bars, last {age:.0f}m ago" if age is not None else f"{sym}: {len(lines)-1} bars"
    if not volcol: bad(msg + " — NO volume column")
    elif live_vol == 0: warn(msg + " — volume column present but all zeros (EA not updated?)")
    else: ok(msg + f", volume live ({live_vol}/{len(vols)} recent bars)")


# ------------------------------------------------------------ 3. signals
head("3. SIGNALS — logged with context, controls kept out of the bot?")
rows = load_signals()
fwd = [r for r in rows if r.get("phase") == "forward"]
res = [r for r in fwd if r.get("R") is not None]
ok(f"{len(rows):,} signals logged ({len(fwd):,} forward, {len(res):,} resolved)")

recent = sorted(fwd, key=lambda r: r.get("ts",""))[-200:]
cov = [r for r in recent if "trend" in r or "vol" in r]
if not recent: warn("no forward signals yet")
elif len(cov) == 0: warn("no covariates on recent signals — live.py v3 not producing yet (needs new signals)")
else: ok(f"covariates present on {len(cov)}/{len(recent)} most recent signals")

held = [r for r in res if r.get("held") is not None]
(ok if held else warn)(f"time-to-resolution recorded on {len(held):,} resolved signals")

sent_p = os.path.join(LOGS, "sent_to_bot.json")
try: sent = set(json.load(open(sent_p)))
except Exception: sent = set()
bad_sent = [k for k in sent if "RANDOM" in k or "RNDLVL" in k or "faded" in k]
if bad_sent: bad(f"{len(bad_sent)} CONTROL signals were sent to the bot (pre-fix history)")
else: ok("no control signals in the bot queue")


# --------------------------------------------------------- 4. evaluation
head("4. EVALUATION — is the verdict machinery running and holdout sealed?")
s = state()
ev = s.get("evaluation")
if not ev:
    bad("state.json has no evaluation block — publish.py stale or not run")
else:
    ok(f"dev {ev['dev_n']:,} / holdout {ev['holdout_n']:,} resolved, "
       f"{ev['tested']} models testable, {len(ev['candidates'])} candidates")
    tot = ev['dev_n'] + ev['holdout_n']
    if tot:
        pc = 100*ev['holdout_n']/tot
        (ok if 25 <= pc <= 35 else warn)(f"holdout is {pc:.1f}% of forward data (target 30%)")
    for m in ev.get("models", []):
        if m.get("verdict","").startswith("CANDIDATE"):
            warn(f"CANDIDATE: {m['model']} (n={m['n']}, {m['exp']:+.2f}R, p={m.get('p')}) "
                 f"— holdout not yet spent")
    revs = os.path.join(LOGS, "holdout_reveals.jsonl")
    if os.path.exists(revs):
        n = sum(1 for _ in open(revs))
        warn(f"holdout has been revealed {n} time(s) — each look is one-shot")
    else:
        ok("holdout never revealed")

geo = {}
for r in res:
    try:
        risk = abs(r["entry"]-r["stop"])
        g = round(abs(r["target"]-r["entry"])/risk, 1) if risk else None
        if g: geo.setdefault(g, set()).add(r["model"])
    except Exception: pass
for g, ms in sorted(geo.items()):
    ctrls = sorted(m for m in ms if "RANDOM" in m)
    real = len(ms) - len(ctrls)
    if ctrls:
        ok(f"geometry {g}R: {real} model(s) vs control {ctrls[0]}")
    else:
        warn(f"geometry {g}R: {real} model(s) with NO matched control")


# ------------------------------------------------------------- 5. alerts
head("5. ALERTS — configured, and would they carry the verdict?")
cfgp = os.path.join(HOME, "alerts.json")
if not os.path.exists(cfgp): bad("alerts.json missing")
else:
    try: c = json.load(open(cfgp))
    except Exception: c = {}
    ok("telegram configured" if c.get("telegram_token") else "telegram NOT configured")
    n_wa = (1 if c.get("wa_key") else 0) + len(c.get("wa_extra", []))
    ok(f"whatsapp recipients: {n_wa}")
    mode = oct(os.stat(cfgp).st_mode)[-3:]
    (ok if mode == "600" else warn)(f"alerts.json permissions {mode} (want 600)")
try:
    sys.path.insert(0, HOME)
    from advice import quality_block
    from alert_extras import usd_lines
    sample = next((r for r in reversed(fwd) if r.get("entry")), None)
    if sample:
        qb = quality_block(sample); ux = usd_lines(sample)
        (ok if qb.strip() else bad)("verdict block renders on a real signal")
        (ok if ux.strip() else warn)(f"money framing renders for {sample.get('sym')}")
    else: warn("no forward signal available to render")
except Exception as e:
    bad(f"alert helpers not importable: {e}")


# --------------------------------------------------------------- 6. cron
head("6. AUTOMATION — is anything actually running on a schedule?")
try:
    cr = subprocess.run(["crontab","-l"], capture_output=True, text=True, timeout=5).stdout
    for name in ("live.py","publish.py","alerts.py","forming.py","watchdog.py"):
        (ok if name in cr else warn)(f"cron: {name} {'scheduled' if name in cr else 'NOT scheduled'}")
except Exception as e:
    warn(f"could not read crontab: {e}")
for svc in ("goldlab","goldlab-ingest"):
    try:
        r = subprocess.run(["systemctl","is-active",svc], capture_output=True, text=True, timeout=5)
        st = r.stdout.strip()
        (ok if st == "active" else bad)(f"service {svc}: {st}")
    except Exception: warn(f"service {svc}: unknown")


# ------------------------------------------------------------ 7. hygiene
head("7. HYGIENE")
fills = os.path.join(LOGS, "demo_fills.jsonl")
if os.path.exists(fills):
    old = new = 0
    for ln in open(fills):
        try: d = json.loads(ln)
        except Exception: continue
        if d.get("ea") == "1.10": new += 1
        else: old += 1
    if old: warn(f"demo_fills.jsonl: {old:,} rows from before the double-order fix — do not analyse")
    (ok if new else warn)(f"demo_fills.jsonl: {new:,} rows from the fixed EA")
conf = os.path.join(LOGS, "confluence.jsonl")
if os.path.exists(conf):
    ms = cs = 0
    for ln in open(conf):
        try: d = json.loads(ln)
        except Exception: continue
        if d.get("kind") == "models": ms += 1
        else: cs += 1
    ok(f"confluence log: {ms} model agreements, {cs} control agreements (the baseline)")
else:
    warn("no confluence log yet — no 2-model agreement has occurred")

print("\n  " + "="*70)
print(f"  {len(PASS)} pass · {len(WARN)} warn · {len(FAIL)} fail")
if FAIL:
    print("\n  Must fix:")
    for m in FAIL: print(f"    - {m}")
print()
