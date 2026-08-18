#!/usr/bin/env python3
"""
fix_gone.py - stop reporting a setup as 'gone' when it actually FIRED.

forming.py watches the open candle. When that candle closes, a surviving
setup becomes a real signal - and forming.json empties because the NEXT
candle has begun. alerts.py read that emptiness as 'the setup vanished'
and announced it, contradicting the signal alert it had just sent.

After this patch alerts.py checks live_signals.jsonl for each remembered
setup. Anything that produced a signal on that candle is reported as
CONFIRMED (or silently dropped); only the ones that genuinely died are
listed as gone.
"""
import os

p = os.path.expanduser("~/goldlab/alerts.py")
src = open(p).read()
orig = src

# store model/sym/dir raw so we can match against the signal log
old = '''    def _label(r):
        ic, nm = NICE.get(str(r.get("sym")), ("", str(r.get("sym"))))
        return (str(r.get("model")) + " \\u00b7 " + nm + " \\u00b7 "
                + str(r.get("dir", "")).upper())

    # remember WHICH candle these belonged to, so "gone" can name it
    _ts = rows[0].get("ts", "") if rows else ""
    nowkey = _ts + "@@" + "|".join(sorted(_label(r) for r in rows))'''
new = '''    def _raw(r):
        return (str(r.get("model")) + "~" + str(r.get("sym")) + "~"
                + str(r.get("dir", "")))

    # remember WHICH candle these belonged to, so "gone" can name it
    _ts = rows[0].get("ts", "") if rows else ""
    nowkey = _ts + "@@" + "|".join(sorted(_raw(r) for r in rows))'''
if old in src:
    src = src.replace(old, new, 1)

old = '''    if not rows:
        if prev:
            pts, _, pbody = prev.partition("@@")
            if not pbody:
                pbody = pts; pts = ""
            gone = ["\\u2022 " + x for x in pbody.split("|") if x.strip()]'''
new = '''    if not rows:
        if prev:
            pts, _, pbody = prev.partition("@@")
            if not pbody:
                pbody = pts; pts = ""
            # which of them actually FIRED on that candle?
            fired = set()
            for s in load_jsonl("live_signals.jsonl")[-600:]:
                if s.get("ts") == pts and s.get("phase") != "backfill":
                    fired.add(str(s.get("model")) + "~" + str(s.get("sym"))
                              + "~" + str(s.get("dir")))
            gone = []
            for x in pbody.split("|"):
                x = x.strip()
                if not x or x in fired:
                    continue                      # it fired: not gone
                bits = x.split("~")
                if len(bits) == 3:
                    ic, nm = NICE.get(bits[1], ("", bits[1]))
                    x = bits[0] + " \\u00b7 " + nm + " \\u00b7 " + bits[2].upper()
                gone.append("\\u2022 " + x)'''
if old in src:
    src = src.replace(old, new, 1)

# reword the footer now that fired setups are excluded
old = '''                 "\\n\\n<i>These were forming and dropped away before the close. "
                 "A signal alert for a different model on the same candle is not "
                 "a contradiction \\u2014 each model is judged on its own.</i>", c)'''
new = '''                 "\\n\\n<i>These were forming and died before the close \\u2014 "
                 "anything that survived has already been sent as a signal, so "
                 "it is not listed here.</i>", c)'''
if old in src:
    src = src.replace(old, new, 1)

if src == orig:
    print("  nothing changed - already patched, or alerts.py differs")
else:
    open(p, "w").write(src)
    import ast; ast.parse(src)
    print("  alerts.py patched:")
    print("   - 'Setup gone' now checks live_signals.jsonl first")
    print("   - setups that FIRED are excluded from the gone list")
    print("   - if every setup fired, no 'gone' alert is sent at all")
