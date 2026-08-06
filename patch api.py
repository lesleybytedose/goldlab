#!/usr/bin/env python3
"""patch_api.py - adds /signals and /live endpoints. Run once from ~/goldlab."""
import re, sys

p = "api.py"
s = open(p).read()

if "def signal_analysis" in s:
    print("already patched"); sys.exit(0)

NEW = '''

# ---------------- signal analysis ----------------

def _tier(title):
    t = (title or "").lower()
    for k in ("non-farm", "nonfarm", "fomc", "federal funds", "cpi", "core pce",
              "powell", "gdp", "unemployment rate"):
        if k in t:
            return "tier1"
    return "high"


def news_gap(when):
    """Minutes from `when` to the nearest high-impact event today."""
    try:
        ev = json.load(open(os.path.join(RAW, "ff_thisweek.json")))
    except Exception:
        return None
    best = None
    for e in ev:
        if str(e.get("impact")) != "High" or e.get("country") not in ("USD", "EUR", "All"):
            continue
        try:
            d = e["date"]
            if d.endswith("Z"):
                d = d[:-1] + "+00:00"
            dt = datetime.fromisoformat(d).astimezone(SAST)
        except Exception:
            continue
        gap = (dt - when).total_seconds() / 60.0
        if best is None or abs(gap) < abs(best[0]):
            best = (gap, e.get("title"), _tier(e.get("title")))
    if best is None:
        return None
    return dict(minutes=round(best[0]), title=best[1], tier=best[2])


def signal_analysis(sig, pool, c):
    """Everything we honestly know about one setup."""
    aligned = bool(sig.get("bias")) and (sig["bias"] == "up") == (sig["direction"] == "long")

    def slice_of(fn, label):
        a = agg([x for x in pool if fn(x)])
        return dict(label=label, **a) if a else None

    same_dir = slice_of(lambda x: x["direction"] == sig["direction"],
                        sig["direction"].title() + " trades")
    same_bias = slice_of(
        lambda x: x.get("bias") and ((x["bias"] == "up") == (x["direction"] == "long")) == aligned,
        "Trend " + ("agrees" if aligned else "against"))
    same_sess = slice_of(lambda x: x.get("session") == sig.get("session"),
                         "Session " + str(sig.get("session")))
    exact = slice_of(
        lambda x: x["direction"] == sig["direction"]
        and x.get("session") == sig.get("session")
        and bool(x.get("bias")) and ((x["bias"] == "up") == (x["direction"] == "long")) == aligned,
        "This exact combination")

    dist = abs(float(sig["entry"]) - float(sig["stop"]))
    risk_money = c["balance"] * c["risk_pct"] / 100.0
    lots = risk_money / (dist * c["contract_oz"]) if dist else 0
    tradeable = lots >= c["min_lot"]
    reward = risk_money * float(sig.get("rr", 0))

    try:
        when = datetime.fromisoformat(sig["ts"])
    except Exception:
        when = datetime.now(SAST)
    ng = news_gap(when)

    reasons, cautions = [], []
    if aligned:
        reasons.append("Trend on the four hour agrees with the direction.")
    else:
        cautions.append("Trend on the four hour points the other way. "
                        "On your data that slice loses money.")
    if float(sig.get("rr", 0)) >= 2:
        reasons.append("Target is more than twice the risk.")
    elif float(sig.get("rr", 0)) < 1.5:
        cautions.append("Reward is under 1.5 times risk. Below your own floor.")
    if sig.get("session") in ("02:00", "08:00", "14:00"):
        reasons.append("Falls inside one of your three sessions.")
    else:
        cautions.append("Outside your sessions. Thinner liquidity, wider spread.")
    if ng and abs(ng["minutes"]) <= 60:
        cautions.append(f"{ng['title']} lands {abs(ng['minutes'])} minutes "
                        f"{'after' if ng['minutes'] > 0 else 'before'} this.")
    if not tradeable:
        cautions.append(f"Account too small — correct size is {lots:.4f} lots, "
                        f"minimum is {c['min_lot']}.")
    if exact and exact["n"] < 30:
        cautions.append(f"Only {exact['n']} past setups match this exactly. "
                        "Too few to draw conclusions from.")

    return dict(
        aligned=aligned,
        slices=[x for x in (exact, same_bias, same_dir, same_sess) if x],
        money=dict(risk=round(risk_money, 2), reward=round(reward, 2),
                   lots=round(lots, 4), tradeable=tradeable,
                   stop_dist=round(dist, 2),
                   spread=round(c["spread"] * max(lots, c["min_lot"]) * c["contract_oz"], 2)),
        news=ng, reasons=reasons, cautions=cautions)


def live_range():
    """The hour currently forming, and whether a raid is under way."""
    b, src = bars(200)
    if len(b) < 12:
        return dict(state="no data")
    def hour_of(t):
        return t[:13]
    groups = {}
    for x in b:
        groups.setdefault(hour_of(x["t"]), []).append(x)
    keys = sorted(groups)
    if len(keys) < 2:
        return dict(state="no data")
    prev, cur = groups[keys[-2]], groups[keys[-1]]
    hi = max(x["h"] for x in prev); lo = min(x["l"] for x in prev)
    chi = max(x["h"] for x in cur); clo = min(x["l"] for x in cur)
    last = cur[-1]["c"]
    state, note = "watching", "Price inside the previous hour's range."
    if clo < lo:
        if last > lo:
            state, note = "raid_low", "Swept below and back inside. Long condition met."
        else:
            state, note = "below", "Below the range and staying there. Not a setup."
    elif chi > hi:
        if last < hi:
            state, note = "raid_high", "Swept above and back inside. Short condition met."
        else:
            state, note = "above", "Above the range and staying there. Not a setup."
    return dict(state=state, note=note, high=round(hi, 2), low=round(lo, 2),
                last=round(last, 2), hour=keys[-1][11:13] + ":00",
                bars_in=len(cur), source=src)
'''

s = s.replace("class H(BaseHTTPRequestHandler):", NEW + "\n\nclass H(BaseHTTPRequestHandler):")

s = s.replace(
    '            if p == "/setups": return self._s(setups()[-400:])',
    '''            if p == "/setups": return self._s(setups()[-400:])
            if p == "/live": return self._s(live_range())
            if p == "/signals":
                pool = setups()
                out = []
                for sg in list(reversed(pool))[:8]:
                    out.append(dict(sg, analysis=signal_analysis(sg, pool, c)))
                return self._s(out)''')

open(p, "w").write(s)
print("api patched")
