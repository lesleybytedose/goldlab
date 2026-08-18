#!/usr/bin/env python3
"""
alert_extras.py - two additions for alerts.py:

1. USD framing for synthetic indices (and anything else): converts the
   R-geometry into money at a stated lot size, using per-symbol contract
   specs. FILL THE SPEC TABLE from the Deriv/Exness terminal:
   right-click symbol -> Specification -> contract size, tick size,
   tick value, minimum lot. Do not guess these numbers.

2. Confluence detection: when 2+ distinct models fire the same symbol and
   direction inside a window, the alert can be escalated. Every agreement
   event is also logged - INCLUDING agreements among the random controls -
   so "do multi-model signals actually win more?" becomes a measurable
   question instead of a feeling.

Wire-up in alerts.py:
    from alert_extras import usd_lines, confluence
    ...
    msg += usd_lines(sig)                       # after the TP block
    n, names, urgent = confluence(sig)
    if urgent:  title = "🚨 URGENT · " + str(n) + " MODELS AGREE"
"""
import json, os
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~/goldlab")
SIGLOG = os.path.join(HOME, "logs/live_signals.jsonl")
CONFLOG = os.path.join(HOME, "logs/confluence.jsonl")
FEED_TZ = timezone.utc

# ------------------------------------------------------------- USD spec
# per 1.0 lot: usd_per_point = contract_size (for cash synthetics this is
# usually tick_value / tick_size). lot = the size you actually trade.
# VERIFY EVERY ROW against the terminal before trusting the numbers.
SPEC = {
    # usd_per_point per 1.0 lot; min lot; lot step. VERIFY EVERY ROW in the
    # terminal: right-click symbol -> Specification. A wrong upp is a wrong
    # dollar risk, silently.
    "Volatility_75_Index":  dict(upp=1.0,   min=0.001, step=0.001),
    "Boom_1000_Index":      dict(upp=1.0,   min=0.20,  step=0.01),
    "Crash_1000_Index":     dict(upp=1.0,   min=0.20,  step=0.01),
    "XAUUSDm":              dict(upp=100.0, min=0.01,  step=0.01),
    "XAUUSD247m":           dict(upp=100.0, min=0.01,  step=0.01),
    "XAGUSD":               dict(upp=50.0,  min=0.01,  step=0.01),
}

RISK_USD = 20.0     # the bet: lot is sized so the stop costs this much

# Deriv multiplier contracts (Deriv Trader, not MT5 lots).
# stake $STAKE at xMULT: profit = stake * mult * price-change%.
# The contract AUTO-CLOSES when loss = stake, i.e. at a 1/mult adverse
# move. If the model stop is further away than that, the contract dies
# before the model stop and the lab's statistics no longer describe the
# trade. The alert computes both and says so.
MULT_MODE = {"Volatility_75_Index", "Boom_1000_Index", "Crash_1000_Index"}
STAKE = 20.0
MULT = 300
MULT_TIERS = [15, 20, 25, 30, 40, 50, 75, 100, 150, 200, 250, 300, 400, 500]


def _mult_lines(sig, html=False):
    try:
        e, st = float(sig["entry"]), float(sig["stop"])
        risk_pts = abs(e - st)
        if risk_pts <= 0 or e <= 0:
            return ""
        stop_pc = risk_pts / e                      # model stop, fraction
        cutoff_pc = 1.0 / MULT                      # forced stop-out
        usd = lambda mult, pc: STAKE * mult * pc
        lines = []
        if stop_pc > cutoff_pc:
            cut_px = e - e*cutoff_pc if st < e else e + e*cutoff_pc
            safe = max([t for t in MULT_TIERS if t <= 1.0/stop_pc] or [MULT_TIERS[0]])
            lines.append(f"x{MULT} stake ${STAKE:.0f}: AUTO-CLOSES at "
                         f"{cut_px:,.2f} ({cutoff_pc:.2%}) - BEFORE the model "
                         f"stop. Lab stats do not apply at x{MULT}.")
            lines.append(f"TP1 +${usd(MULT, stop_pc*1.0):,.2f}   "
                         f"TP2 +${usd(MULT, stop_pc*1.5):,.2f}   "
                         f"TP3 +${usd(MULT, stop_pc*2.0):,.2f}   (if it survives)")
            lines.append(f"geometry-safe: x{safe} -> stop -${usd(safe, stop_pc):,.2f}, "
                         f"TP1 +${usd(safe, stop_pc):,.2f}  "
                         f"TP2 +${usd(safe, stop_pc*1.5):,.2f}  "
                         f"TP3 +${usd(safe, stop_pc*2.0):,.2f}")
        else:
            lines.append(f"x{MULT} stake ${STAKE:.0f}: model stop = "
                         f"-${usd(MULT, stop_pc):,.2f} (within stake)")
            lines.append(f"TP1 +${usd(MULT, stop_pc*1.0):,.2f}   "
                         f"TP2 +${usd(MULT, stop_pc*1.5):,.2f}   "
                         f"TP3 +${usd(MULT, stop_pc*2.0):,.2f}")
        body = "\n".join(lines)
        return "\n" + (f"<i>{body}</i>" if html else body)
    except Exception:
        return ""


def usd_lines(sig, html=False):
    if sig.get("sym") in MULT_MODE:
        return _mult_lines(sig, html)
    return _lot_lines(sig, html)


def _lot_lines(sig, html=False):
    """Size the lot so the stop costs RISK_USD, then price the TP ladder
    in dollars at that lot. If the symbol's minimum lot forces a bigger
    bet than RISK_USD, say so instead of pretending."""
    sp = SPEC.get(sig.get("sym", ""))
    if not sp:
        return ""
    try:
        e, st = float(sig["entry"]), float(sig["stop"])
        risk_pts = abs(e - st)
        if risk_pts <= 0:
            return ""
        raw = RISK_USD / (risk_pts * sp["upp"])
        lot = max(sp["min"], round(raw / sp["step"]) * sp["step"])
        lot = round(lot, 3)
        actual = risk_pts * sp["upp"] * lot
        usd = lambda mult: risk_pts * mult * sp["upp"] * lot
        lines = [f"${RISK_USD:.0f} bet -> lot {lot:g}   (stop = ${actual:,.2f})"]
        if actual > RISK_USD * 1.5:
            lines[0] = (f"min lot {sp['min']:g} forces a ${actual:,.2f} bet "
                        f"(wanted ${RISK_USD:.0f}) - consider skipping")
        lines.append(f"TP1 +${usd(1.0):,.2f}   TP2 +${usd(1.5):,.2f}   "
                     f"TP3 +${usd(2.0):,.2f}")
        body = "\n".join(lines)
        return "\n" + (f"<i>{body}</i>" if html else body)
    except Exception:
        return ""


# --------------------------------------------------------- confluence
WINDOW_MIN = 45          # models must fire within this many minutes
URGENT_AT = 2            # distinct non-control models for escalation


def _recent(sym, ts, minutes=WINDOW_MIN):
    try:
        t0 = datetime.fromisoformat(ts)
    except Exception:
        return []
    lo = t0 - timedelta(minutes=minutes)
    out = []
    if not os.path.exists(SIGLOG):
        return out
    for ln in open(SIGLOG):
        try:
            r = json.loads(ln)
            if r.get("sym") != sym or r.get("phase") == "backfill":
                continue
            t = datetime.fromisoformat(r["ts"])
            if lo <= t <= t0:
                out.append(r)
        except Exception:
            pass
    return out


def confluence(sig):
    """Returns (n_models, names, urgent). Logs every agreement event, and
    separately logs control-agreement so the urgent flag has a baseline."""
    sym, d, ts = sig.get("sym"), sig.get("dir"), sig.get("ts", "")
    near = _recent(sym, ts)
    agree = sorted({r["model"] for r in near
                    if r.get("dir") == d and "RANDOM" not in r.get("model", "")})
    ctrl_agree = sorted({r["model"] for r in near
                         if r.get("dir") == d and "RANDOM" in r.get("model", "")})
    n = len(agree)
    urgent = n >= URGENT_AT
    if n >= 2 or len(ctrl_agree) >= 2:
        try:
            with open(CONFLOG, "a") as f:
                f.write(json.dumps(dict(
                    sym=sym, dir=d, ts=ts, models=agree,
                    controls_agreeing=ctrl_agree,
                    kind=("models" if n >= 2 else "controls"),
                    at=datetime.now(FEED_TZ).isoformat(timespec="seconds"))) + "\n")
        except Exception:
            pass
    return n, agree, urgent


def urgent_header(n, names, html=False):
    who = " + ".join(names)
    t = f"🚨 URGENT · SAME CALL FROM {n} MODELS\n{who}"
    note = ("models can be cousins — agreement is interest, not proof")
    if html:
        return f"<b>{t}</b>\n<i>{note}</i>\n"
    return f"{t}\n({note})\n"


if __name__ == "__main__":
    # self-test on whatever log exists
    fake = dict(sym="XAUUSDm", dir="long", ts="2026-06-30T22:00:00+00:00",
                entry=2400.0, stop=2398.6)
    print(usd_lines(fake))
    n, names, urgent = confluence(fake)
    print("confluence:", n, names, "urgent:", urgent)
    if urgent:
        print(urgent_header(n, names))
