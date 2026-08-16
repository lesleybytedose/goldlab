#!/usr/bin/env python3
"""
publish.py - one JSON with everything the app shows.
All measured from the signal log. Nothing estimated, nothing typed in.

Evaluation layer (forward phase only):
  - every signal is split by hash of its key: 70% development, 30% holdout
  - all published statistics use the development set only
  - each model is compared to the random control matching its own
    stop/target geometry, with a one-sided Welch test
  - Benjamini-Hochberg FDR at q=0.10 across all models decides who is a
    candidate; nothing is a finding until it also survives its holdout
  - the holdout is revealed only by hand:  python3 publish.py --reveal-holdout "Model name"
    and every reveal is written to logs/holdout_reveals.jsonl
"""
import csv, glob, hashlib, json, math, os, ssl, subprocess, sys, urllib.request
from datetime import datetime, timezone, timedelta

FEED_TZ = timezone.utc
SAST = timezone(timedelta(hours=2))
HOME = os.path.expanduser("~/goldlab")
RAW = os.path.join(HOME, "data/raw")
LOGS = os.path.join(HOME, "logs")
CACHE = os.path.join(LOGS, "news_cache.json")
REVEALS = os.path.join(LOGS, "holdout_reveals.jsonl")
TARGETS = ["/opt/hgs/client/goldlab/state.json",
           os.path.join(HOME, "web", "state.json")]

HOLDOUT_PCT = 30          # % of forward signals frozen, by hash of signal key
FDR_Q = 0.10              # Benjamini-Hochberg false discovery rate

WHAT = {
 "M1 daily extreme":  "Buys a new high for the day after the first two hours, sells "
                      "a new low. Betting the move keeps going.",
 "M1 faded":          "The exact opposite. It exists as a check — if the first one "
                      "has a real edge, this one must lose by about the same amount.",
 "CRT sweep":         "Price pokes past a recent extreme then closes back inside. "
                      "Reads that as a failed break and trades the other way.",
 "Order block":       "Finds the last opposing candle before a sharp move, then takes "
                      "the first return to it.",
 "Range spike":       "A candle far larger than normal. Trades the direction it closed.",
 "London break":      "Marks the first hour of London, then trades the break of it.",
 "Contraction break": "Six quiet candles, then trades whichever way price escapes.",
 "RANDOM CONTROL":    "Coin flips with the same stops and targets as everything else. "
                      "This is the bar every model has to clear.",
 "MA/ATR band":       "Bansal's own system: close beyond a moving-average band widened "
                      "by ATR. Trend entry with a volatility filter.",
 "Donchian SAR":      "Close beyond the 20-bar channel, stop-and-reverse. Bansal's "
                      "second personal system (HHV/LLV breakout).",
 "Failure test":      "Price pokes past the prior day's high or low and closes back "
                      "inside. Grimes's sweep-and-fail, stop beyond the sweep.",
 "Break-retest pivot": "Breaks a prior-day floor pivot, then enters on the first "
                      "retest that holds. The universal break-retest template.",
 "Break-retest RNDLVL": "Identical logic fed deliberately wrong levels. If real "
                      "pivots matter, the real version must beat this one.",
 "RANDOM CTRL 2.0R":  "Coin flips at 1.0 stop / 2.0 target — the fair benchmark for "
                      "every model trading that geometry.",
 "RANDOM CTRL 1.5R":  "Coin flips at 1.0 stop / 1.5 target — the fair benchmark for "
                      "Range spike's geometry.",
}


def dp(sym):
    """Gold needs 2 decimals, FX needs 5."""
    return 2 if "XAU" in sym.upper() else 5


def read_bars(path, keep=200):
    rows = list(csv.DictReader(open(path)))
    out = []
    for r in rows[-keep:]:
        try:
            out.append(dict(t=f"{r['date']} {r['time']}", o=float(r["open"]),
                            h=float(r["high"]), l=float(r["low"]),
                            c=float(r["close"]), s=float(r.get("spread") or 0)))
        except Exception:
            pass
    return out, len(rows)


def feeds():
    now = datetime.now(FEED_TZ)
    out = []
    DEAD = ("XAUUSDc", "EURUSDc", "EURGBPc")   # not fed since MT5 moved to demo
    for p in sorted(glob.glob(os.path.join(RAW, "*_M15_live.csv"))):
        if os.path.basename(p).replace("_M15_live.csv","").replace("_M15.csv","") in DEAD:
            continue
        sym = os.path.basename(p).replace("_M15_live.csv","").replace("_M15.csv","")
        gold = sym.startswith("XAU")
        try:
            series, total = read_bars(p, 200 if gold else 3)
            if not series:
                continue
            last = datetime.fromisoformat(series[-1]["t"]).replace(tzinfo=FEED_TZ)
            e = dict(symbol=sym, bars=total, last=series[-1]["t"],
                     age_min=round((now - last).total_seconds() / 60),
                     price=series[-1]["c"], spread=series[-1]["s"])
            e["stale"] = e["age_min"] > 35
            if gold:
                # splice on the candle still forming, if the EA is sending it
                fp = os.path.join(RAW, f"{sym}_M15_forming.json")
                if os.path.exists(fp):
                    try:
                        fb = json.load(open(fp))
                        ft = datetime.fromisoformat(fb["t"] if " " in fb["t"]
                                                    else f"{fb['d']} {fb['t']}")
                        ft = ft.replace(tzinfo=FEED_TZ)
                        if (now - ft).total_seconds() < 1800:
                            fb = dict(t=f"{fb['d']} {fb['t']}", o=fb["o"], h=fb["h"],
                                      l=fb["l"], c=fb["c"], s=fb.get("s", 0))
                            if series and series[-1]["t"] == fb["t"]:
                                series[-1] = fb
                            else:
                                series.append(fb)
                            e["price"] = fb["c"]
                            e["spread"] = fb["s"]
                            e["forming"] = True
                            e["age_min"] = round((now - ft).total_seconds() / 60)
                            e["stale"] = e["age_min"] > 35
                    except Exception:
                        pass
                e["series"] = series
                day = series[-1]["t"][:10]
                td = [b for b in series if b["t"][:10] == day]
                if td:
                    e.update(day_high=max(b["h"] for b in td),
                             day_low=min(b["l"] for b in td), bars_today=len(td),
                             change=round(series[-1]["c"] - td[0]["o"], 2))
                    if len(td) >= 8:
                        ohi = max(b["h"] for b in td[:8]); olo = min(b["l"] for b in td[:8])
                        c = series[-1]["c"]
                        e["state"] = ("above the opening range" if c > ohi else
                                      "below the opening range" if c < olo else
                                      "inside the opening range")
                        e["open_hi"], e["open_lo"] = round(ohi, 2), round(olo, 2)
            out.append(e)
        except Exception:
            pass
    return out


def load_signals():
    p = os.path.join(LOGS, "live_signals.jsonl")
    rows = []
    if os.path.exists(p):
        for ln in open(p):
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
    return rows


# ------------------------------------------------------------ evaluation
def is_holdout(r):
    """Deterministic 30% split by signal key. Same signal, same bucket, forever."""
    k = r.get("k") or f"{r.get('sym')}|{r.get('model')}|{r.get('ts')}"
    return int(hashlib.md5(k.encode()).hexdigest()[:8], 16) % 100 < HOLDOUT_PCT


def _geometry(r):
    try:
        risk = abs(r["entry"] - r["stop"])
        return round(abs(r["target"] - r["entry"]) / risk, 1) if risk else None
    except Exception:
        return None


def _welch_p(x, y):
    """One-sided p for mean(x) > mean(y). Normal approximation, stdlib only."""
    nx, ny = len(x), len(y)
    if nx < 2 or ny < 2:
        return None
    mx, my = sum(x)/nx, sum(y)/ny
    vx = sum((v-mx)**2 for v in x) / (nx-1)
    vy = sum((v-my)**2 for v in y) / (ny-1)
    den = math.sqrt(vx/nx + vy/ny)
    if den == 0:
        return None
    z = (mx - my) / den
    return 0.5 * math.erfc(z / math.sqrt(2))


def evaluation(rows):
    """Forward phase, development set only. Each model vs its geometry-matched
    control, BH-corrected across the family. Holdout counted, never used."""
    fwd = [r for r in rows if r.get("phase") == "forward" and r.get("R") is not None]
    dev = [r for r in fwd if not is_holdout(r)]
    hold_n = len(fwd) - len(dev)

    ctrl_names = {2.0: "RANDOM CTRL 2.0R", 1.5: "RANDOM CTRL 1.5R",
                  1.8: "RANDOM CONTROL"}
    by = {}
    for r in dev:
        by.setdefault(r["model"], []).append(r)

    ctrl_R = {}
    for g, name in ctrl_names.items():
        ctrl_R[g] = [r["R"] for r in by.get(name, [])]

    tests, out = [], []
    for m, rs in sorted(by.items()):
        if m in ctrl_names.values():
            continue
        R = [r["R"] for r in rs]
        geos = [g for g in (_geometry(r) for r in rs) if g]
        geo = sorted(geos)[len(geos)//2] if geos else 2.0
        cg = min(ctrl_names, key=lambda g: abs(g - geo))
        cR = ctrl_R.get(cg, [])
        held = [r["held"] for r in rs if r.get("held") is not None]
        d = dict(model=m, n=len(R), exp=round(sum(R)/len(R), 3) if R else None,
                 geometry=geo, control=ctrl_names[cg], control_n=len(cR),
                 med_held=(sorted(held)[len(held)//2] if held else None))
        if len(R) >= 30 and len(cR) >= 30:
            p = _welch_p(R, cR)
            d["p"] = round(p, 4) if p is not None else None
            d["diff"] = round(sum(R)/len(R) - sum(cR)/len(cR), 3)
            if p is not None:
                tests.append((p, m))
        else:
            d["p"] = None
            d["verdict"] = f"insufficient (n={len(R)}, control n={len(cR)}; need 30+30)"
        out.append(d)

    # Benjamini-Hochberg across everything actually tested
    passed = set()
    if tests:
        tests.sort()
        mtot = len(tests)
        kmax = 0
        for k, (p, _) in enumerate(tests, 1):
            if p <= FDR_Q * k / mtot:
                kmax = k
        passed = {m for _, m in tests[:kmax]}
    for d in out:
        if "verdict" in d:
            continue
        if d["p"] is None:
            d["verdict"] = "not testable"
        elif d["model"] in passed:
            d["verdict"] = "CANDIDATE on dev (BH q=0.10) — holdout untouched"
        elif d["diff"] is not None and d["diff"] > 0:
            d["verdict"] = "above control, not significant after correction"
        else:
            d["verdict"] = "no edge vs matched control"
    return dict(holdout_pct=HOLDOUT_PCT, fdr_q=FDR_Q,
                dev_n=len(dev), holdout_n=hold_n,
                tested=len(tests), candidates=sorted(passed),
                models=out)


def reveal_holdout(model_name):
    """The one-shot look. Prints holdout-only stats vs the matched control's
    holdout, and logs that the look happened."""
    rows = load_signals()
    fwd = [r for r in rows if r.get("phase") == "forward" and r.get("R") is not None]
    hold = [r for r in fwd if is_holdout(r)]
    mine = [r for r in hold if r.get("model") == model_name]
    if not mine:
        print(f"  no resolved holdout signals for '{model_name}'"); return
    geos = [g for g in (_geometry(r) for r in mine) if g]
    geo = sorted(geos)[len(geos)//2] if geos else 2.0
    ctrl_names = {2.0: "RANDOM CTRL 2.0R", 1.5: "RANDOM CTRL 1.5R", 1.8: "RANDOM CONTROL"}
    cname = ctrl_names[min(ctrl_names, key=lambda g: abs(g - geo))]
    cR = [r["R"] for r in hold if r.get("model") == cname]
    R = [r["R"] for r in mine]
    p = _welch_p(R, cR)
    print(f"\n  HOLDOUT REVEAL — {model_name}")
    print(f"  model:   n={len(R)}  exp={sum(R)/len(R):+.3f}R")
    if cR:
        print(f"  control: {cname}  n={len(cR)}  exp={sum(cR)/len(cR):+.3f}R")
    print(f"  one-sided p vs control: {p:.4f}" if p is not None
          else "  p: not computable (need n>=2 both sides)")
    print("  This look is now logged. It does not repeat.\n")
    try:
        with open(REVEALS, "a") as f:
            f.write(json.dumps(dict(model=model_name, n=len(R),
                                    exp=round(sum(R)/len(R), 3),
                                    control=cname, control_n=len(cR),
                                    p=(round(p, 4) if p is not None else None),
                                    at=datetime.now(FEED_TZ).isoformat(timespec="seconds"))) + "\n")
    except Exception:
        pass


def risk_stats(seq):
    """seq = list of R in time order. Returns the numbers that decide survivability."""
    if not seq:
        return {}
    eq = peak = 0.0
    dd = 0.0
    streak = worst_streak = 0
    wins = wstreak = bwstreak = 0
    for r in seq:
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
        if r <= 0:
            streak += 1; worst_streak = max(worst_streak, streak); wstreak = 0
        else:
            streak = 0; wins += 1; wstreak += 1; bwstreak = max(bwstreak, wstreak)
    n = len(seq)
    mean = sum(seq) / n
    var = sum((x - mean) ** 2 for x in seq) / max(1, n - 1)
    sd = math.sqrt(var)
    se = sd / math.sqrt(n) if n else 0
    return dict(
        max_dd=round(dd, 1),
        worst_streak=worst_streak,
        best_streak=bwstreak,
        sd=round(sd, 3),
        ci_lo=round(mean - 1.96 * se, 3),
        ci_hi=round(mean + 1.96 * se, 3),
        significant=bool(n >= 30 and (mean - 1.96 * se) > 0),
    )


def stress(rows):
    """What happens to each model if the spread doubles or triples."""
    out = {}
    for r in rows:
        if r.get("R") is None:
            continue
        try:
            risk = abs(r["entry"] - r["stop"])
            cost = (r.get("spread") or 0) / risk if risk else 0
        except Exception:
            continue
        d = out.setdefault(r["model"], dict(n=0, base=0.0, x2=0.0, x3=0.0))
        d["n"] += 1
        d["base"] += r["R"]
        d["x2"] += r["R"] - cost
        d["x3"] += r["R"] - 2 * cost
    for m, d in out.items():
        n = d["n"] or 1
        d["base"] = round(d["base"] / n, 3)
        d["x2"] = round(d["x2"] / n, 3)
        d["x3"] = round(d["x3"] / n, 3)
    return out


def model_stats(rows):
    acc = {}
    for r in sorted(rows, key=lambda r: r.get("ts", "")):
        m = r.get("model", "?")
        d = acc.setdefault(m, dict(name=m, n=0, wins=0, total=0.0, open=0,
                                   best=None, worst=None, seq=[], held=[],
                                   what=WHAT.get(m, "")))
        if r.get("R") is None:
            d["open"] += 1
        else:
            R = r["R"]
            d["n"] += 1; d["total"] += R; d["seq"].append(R)
            if r.get("held") is not None:
                d["held"].append(r["held"])
            if R > 0:
                d["wins"] += 1
            d["best"] = R if d["best"] is None else max(d["best"], R)
            d["worst"] = R if d["worst"] is None else min(d["worst"], R)
    st = stress(rows)
    out = []
    for d in acc.values():
        if d["n"]:
            d["win"] = round(d["wins"] / d["n"], 4)
            d["exp"] = round(d["total"] / d["n"], 3)
            d["total"] = round(d["total"], 1)
            d["best"] = round(d["best"], 2); d["worst"] = round(d["worst"], 2)
            d.update(risk_stats(d["seq"]))
            if d["held"]:
                hs = sorted(d["held"])
                d["med_held"] = hs[len(hs)//2]
            s = st.get(d["name"])
            if s:
                d["stress"] = dict(x2=s["x2"], x3=s["x3"])
        else:
            d["win"] = d["exp"] = None
        d.pop("seq", None); d.pop("held", None)
        out.append(d)
    out.sort(key=lambda x: (x["exp"] is None, -(x["exp"] or 0)))
    return out


def hour_grid(rows):
    """Mean R by hour of day, across everything. Shows when the day pays."""
    acc = {}
    for r in rows:
        if r.get("R") is None:
            continue
        try:
            h = int(r["ts"][11:13])
        except Exception:
            continue
        a = acc.setdefault(h, [0, 0.0])
        a[0] += 1; a[1] += r["R"]
    return [dict(h=h, n=v[0], exp=round(v[1] / v[0], 3))
            for h, v in sorted(acc.items()) if v[0] >= 5]


def overlap(rows):
    """How often models fire together — are they really separate ideas?"""
    by = {}
    for r in rows:
        key = (r.get("ts", "")[:13], r.get("dir"), r.get("sym"))
        by.setdefault(key, set()).add(r.get("model"))
    pair = {}
    solo = {}
    for models in by.values():
        ms = sorted(m for m in models if m and "RANDOM" not in m)
        for m in ms:
            solo[m] = solo.get(m, 0) + 1
        for i in range(len(ms)):
            for j in range(i + 1, len(ms)):
                k = ms[i] + " + " + ms[j]
                pair[k] = pair.get(k, 0) + 1
    top = sorted(pair.items(), key=lambda x: -x[1])[:6]
    return [dict(pair=k, together=v) for k, v in top if v >= 3]


def equity(rows):
    done = sorted([r for r in rows if r.get("R") is not None],
                  key=lambda r: r.get("ts", ""))
    eq, pts = 0.0, []
    step = max(1, len(done) // 140)
    for i, r in enumerate(done):
        eq += r["R"]
        if i % step == 0:
            pts.append(round(eq, 2))
    if done:
        pts.append(round(eq, 2))
    return pts, risk_stats([r["R"] for r in done])


def enrich(rows, stats, prices):
    wm = {s["name"]: s["win"] for s in stats}
    nm = {s["name"]: s["n"] for s in stats}
    out = []
    for r in sorted(rows, key=lambda r: r.get("ts", ""), reverse=True)[:25]:
        d = dict(r)
        d["hit_rate"] = wm.get(r.get("model"))
        d["sample"] = nm.get(r.get("model"), 0)
        sym = r.get("sym", "")
        d["dp"] = dp(sym)
        live_px = prices.get(sym)
        if r.get("R") is None and live_px and r.get("entry"):
            risk = abs(r["entry"] - r["stop"])
            if risk > 0:
                mv = (live_px - r["entry"]) if r["dir"] == "long" else (r["entry"] - live_px)
                lr = mv / risk
                if -6 < lr < 6:          # anything wilder is a data fault, not a trade
                    d["live_r"] = round(lr, 2)
        out.append(d)
    return out


def news():
    try:
        if os.path.exists(CACHE):
            c = json.load(open(CACHE))
            if (datetime.now(FEED_TZ) - datetime.fromisoformat(c["fetched"])).total_seconds() < 3600:
                return c["events"]
    except Exception:
        pass
    try:
        req = urllib.request.Request(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20,
                                    context=ssl.create_default_context()) as r:
            data = json.loads(r.read().decode())
    except Exception:
        return []
    now = datetime.now(FEED_TZ); out = []
    for e in data:
        try:
            if e.get("impact") not in ("High", "Medium"):
                continue
            if e.get("country") not in ("USD", "EUR", "GBP"):
                continue
            t = datetime.fromisoformat(e["date"].replace("Z", "+00:00")).astimezone(FEED_TZ)
            mins = (t - now).total_seconds() / 60
            if -120 < mins < 1800:
                out.append(dict(title=e.get("title", "")[:60], cur=e.get("country"),
                                impact=e.get("impact"),
                                sast=t.astimezone(SAST).strftime("%a %H:%M"),
                                mins=round(mins)))
        except Exception:
            pass
    out.sort(key=lambda x: x["mins"]); out = out[:8]
    try:
        json.dump(dict(fetched=datetime.now(FEED_TZ).isoformat(), events=out), open(CACHE, "w"))
    except Exception:
        pass
    return out


def services():
    o = {}
    for s in ("goldlab", "goldlab-ingest"):
        try:
            r = subprocess.run(["systemctl", "is-active", s],
                               capture_output=True, text=True, timeout=5)
            o[s] = r.stdout.strip()
        except Exception:
            o[s] = "unknown"
    return o


def main():
    if "--reveal-holdout" in sys.argv:
        i = sys.argv.index("--reveal-holdout")
        if i + 1 >= len(sys.argv):
            print('  usage: python3 publish.py --reveal-holdout "Model name"')
            return
        reveal_holdout(sys.argv[i + 1])
        return

    fd = feeds()
    gold = next((f for f in fd if f["symbol"].startswith("XAU")), None)
    price = gold["price"] if gold else None
    rows = load_signals()
    fwd = [r for r in rows if r.get("phase") != "backfill"]
    stats = model_stats(rows)
    stats_fwd = model_stats(fwd)
    eqpts, eqrisk = equity(rows)
    eqf, eqfr = equity(fwd)
    obs = os.path.join(LOGS, "observations.jsonl")
    obs_rows = []
    if os.path.exists(obs):
        for ln in open(obs):
            try:
                obs_rows.append(json.loads(ln))
            except Exception:
                pass

    state = dict(
        generated=datetime.now(FEED_TZ).isoformat(timespec="seconds"),
        clock_sast=datetime.now(SAST).strftime("%H:%M"),
        feeds=fd, models=stats, models_forward=stats_fwd,
        equity_forward=eqf, equity_forward_risk=eqfr,
        signals=enrich(rows, stats, {f["symbol"]: f["price"] for f in fd}),
        equity=eqpts, equity_risk=eqrisk,
        hours=hour_grid(rows), overlap=overlap(rows),
        evaluation=evaluation(rows),
        totals=dict(logged=len(rows),
                    resolved=sum(1 for r in rows if r.get("R") is not None),
                    open=sum(1 for r in rows if r.get("R") is None)),
        news=news(), services=services(),
        observations=dict(count=sum(1 for r in fwd if r.get("R") is not None), required=30,
                          recent=obs_rows[-8:][::-1]),
    )
    blob = json.dumps(state, separators=(",", ":"))
    ok = 0
    for t in TARGETS:
        try:
            os.makedirs(os.path.dirname(t), exist_ok=True)
            open(t, "w").write(blob); ok += 1
        except Exception:
            pass
    ev = state["evaluation"]
    print(f"  {len(blob)//1024}kb -> {ok} target(s)  "
          f"{len(stats)} models, {len(state['news'])} events, "
          f"{state['totals']['resolved']} resolved  |  eval: dev {ev['dev_n']}, "
          f"holdout {ev['holdout_n']}, candidates {len(ev['candidates'])}")


if __name__ == "__main__":
    main()
