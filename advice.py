#!/usr/bin/env python3
"""
advice.py - the honest broker for alerts.

alerts.py calls quality_block(sig) and appends the result to any signal
message. The block tells the reader what the evidence actually says about
this model right now, in three or four short lines, instead of letting a
fresh pattern look like a proven one.

Everything is read from web/state.json (already generated every minute),
so this file needs no network, no log parsing, and adds ~0ms.

    from advice import quality_block
    msg += quality_block(sig)                    # plain text (WhatsApp)
    msg += quality_block(sig, html=True)         # Telegram HTML
"""
import json, os

STATE = os.path.expanduser("~/goldlab/web/state.json")


def _state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def quality_block(sig, html=False):
    s = _state()
    model = sig.get("model", "")
    lines = []

    # 1) the verdict — from the BH-corrected evaluation, dev set only
    ev = {m["model"]: m for m in s.get("evaluation", {}).get("models", [])}
    e = ev.get(model)
    if e:
        v = e.get("verdict", "")
        n = e.get("n", 0)
        exp = e.get("exp")
        if "CANDIDATE" in v:
            lines.append(f"Verdict: CANDIDATE on dev (n={n}, {exp:+.2f}R) — holdout still sealed")
        elif "insufficient" in v:
            lines.append(f"Verdict: unproven — only {n} resolved forward trades (need 30)")
        elif "no edge" in v:
            lines.append(f"Verdict: NO edge vs matched control (n={n}, {exp:+.2f}R)")
        elif v:
            lines.append(f"Verdict: {v} (n={n})")
    elif "RANDOM" in model:
        lines.append("This is a control. It exists to be the bar, not to be traded.")
    else:
        lines.append("Verdict: no forward record yet")

    # 2) context flags stamped on the signal itself
    flags = []
    if sig.get("with_trend") is False:
        flags.append("AGAINST the 200-bar trend")
    if sig.get("vol") == "quiet":
        flags.append("quiet regime")
    elif sig.get("vol") == "expanding":
        flags.append("expanding regime")
    if sig.get("into_level"):
        t = sig.get("lvl_touches", 0)
        side = sig.get("lvl_side", "level")
        flags.append(f"entering INTO {side} price has already tested {t}x "
                     f"({sig.get('lvl_bars_at',0)} bars sitting on it)")
    hr = sig.get("hr_atr")
    if hr is not None and sig.get("hr_n"):
        flags.append(f"{sig['hr_n']}-rule level {hr:.1f} ATR ahead, inside the "
                     f"target ({sig.get('hr_src','').split('|')[0]})")
    sp = sig.get("spread_pct")
    if sp is not None and sp >= 0.25:
        flags.append(f"spread eats {sp:.0%} of risk")
    # hour history: only speak when that hour has real sample
    try:
        hr = int(str(sig.get("ts", ""))[11:13])
        hrow = next((h for h in s.get("hours", []) if h["h"] == hr), None)
        if hrow and hrow["n"] >= 30 and hrow["exp"] <= -0.10:
            flags.append(f"{hr:02d}:00 UTC has paid {hrow['exp']:+.2f}R historically")
    except Exception:
        pass
    if flags:
        lines.append("Caution: " + "; ".join(flags))

    # 3) the stage line — never omitted
    lines.append("Stage 1 — log it, do not trade it."
                 if not (e and "CANDIDATE" in e.get("verdict", ""))
                 else "Stage 2 — candidate. Demo only until the holdout is spent.")

    if html:
        return "\n" + "\n".join(f"<i>{l}</i>" for l in lines)
    return "\n" + "\n".join(lines)


if __name__ == "__main__":
    # self-test against whatever state.json exists
    fake = dict(model="CRT sweep", ts="2026-08-16T09:00:00+00:00",
                with_trend=False, vol="quiet", spread_pct=0.31)
    print(quality_block(fake))
    print(quality_block(dict(model="RANDOM CONTROL", ts="")))
