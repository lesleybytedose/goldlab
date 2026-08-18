#!/usr/bin/env python3
"""
fix_stale.py - do not report 'setup gone' when the DATA went missing.

forming.py's scan() returns [] both when nothing is forming AND when the
EA's live-candle file is missing or stale. alerts.py cannot tell those
apart, so a one-minute feed hiccup looks exactly like a setup dying.

This adds a data_ok flag to forming.json (true only if at least one symbol
had a fresh live candle) and makes alerts.py stay quiet when it is false.
"""
import os

# ---------------- forming.py: publish a data_ok flag --------------------
fp = os.path.expanduser("~/goldlab/forming.py")
src = open(fp).read()
o1 = src
old = '''    payload = dict(
        generated=now.isoformat(timespec="seconds"),
        minutes_left=mins_left,
        forming=found,
        approaching=near,
    )'''
new = '''    # did ANY symbol give us a fresh live candle? if not, an empty
    # "forming" list means missing data, not a setup that died.
    data_ok = False
    for s in syms:
        _b, _t = with_forming(s)
        if _t is not None:
            data_ok = True
            break
    payload = dict(
        generated=now.isoformat(timespec="seconds"),
        minutes_left=mins_left,
        data_ok=data_ok,
        symbols_live=len(syms),
        forming=found,
        approaching=near,
    )'''
if old in src:
    src = src.replace(old, new, 1)
    open(fp, "w").write(src)
    import ast; ast.parse(src)
    print("  forming.py: publishes data_ok + symbols_live")
else:
    print("  forming.py: already patched or differs")

# ---------------- alerts.py: respect the flag ---------------------------
ap = os.path.expanduser("~/goldlab/alerts.py")
src = open(ap).read()
o2 = src
old = '''    rows = [r for r in (d.get("forming") or [])
            if not is_control(r.get("model"))]'''
new = '''    rows = [r for r in (d.get("forming") or [])
            if not is_control(r.get("model"))]
    # an empty list with no live candle data means the FEED went quiet,
    # not that a setup died. Say nothing rather than something wrong.
    if not rows and d.get("data_ok") is False:
        return'''
if old in src:
    src = src.replace(old, new, 1)
    open(ap, "w").write(src)
    import ast; ast.parse(src)
    print("  alerts.py: silent when forming data is unavailable")
else:
    print("  alerts.py: already patched or differs")
