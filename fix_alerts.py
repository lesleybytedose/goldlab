#!/usr/bin/env python3
"""One-shot patch for alerts.py: separators, candle identity, controls out."""
import os, re, sys
p = os.path.expanduser("~/goldlab/alerts.py")
src = open(p).read()
orig = src

# ---------- 1. controls never appear in forming or conflict --------------
if "def is_control" not in src:
    src = src.replace(
        "def next_news(win=45):",
        'def is_control(m):\n'
        '    """Controls are measured, never watched or traded."""\n'
        '    m = str(m)\n'
        '    return ("RANDOM" in m) or ("RNDLVL" in m) or ("faded" in m)\n'
        '\n'
        '\n'
        'def next_news(win=45):', 1)

# conflict line must ignore controls
old = '''        opp = [o for o in sigs
               if o.get("sym") == r.get("sym") and o.get("ts") == r.get("ts")
               and o.get("dir") != r.get("dir")]'''
new = '''        opp = [o for o in sigs
               if o.get("sym") == r.get("sym") and o.get("ts") == r.get("ts")
               and o.get("dir") != r.get("dir")
               and not is_control(o.get("model"))]'''
if old in src:
    src = src.replace(old, new, 1)

# ---------- 2. forming: filter controls, readable keys, candle identity ---
old = '''    rows = d.get("forming") or []
    left = d.get("minutes_left", 0)
    marker = os.path.join(LOGS, "forming_last.txt")
    prev = open(marker).read().strip() if os.path.exists(marker) else ""
    nowkey = "|".join(sorted(r["model"] + r["sym"] + r["dir"] for r in rows))'''
new = '''    rows = [r for r in (d.get("forming") or [])
            if not is_control(r.get("model"))]
    left = d.get("minutes_left", 0)
    marker = os.path.join(LOGS, "forming_last.txt")
    prev = open(marker).read().strip() if os.path.exists(marker) else ""

    def _label(r):
        ic, nm = NICE.get(str(r.get("sym")), ("", str(r.get("sym"))))
        return (str(r.get("model")) + " \\u00b7 " + nm + " \\u00b7 "
                + str(r.get("dir", "")).upper())

    # remember WHICH candle these belonged to, so "gone" can name it
    _ts = rows[0].get("ts", "") if rows else ""
    nowkey = _ts + "@@" + "|".join(sorted(_label(r) for r in rows))'''
if old in src:
    src = src.replace(old, new, 1)

# ---------- 3. the "gone" message names the candle -----------------------
old = '''    if not rows:
        if prev:
            gone = []
            for part in prev.split("|"):
                if part.strip():
                    gone.append("\\u2022 " + part)
            detail = (chr(10).join(gone) if gone else "")
            send("Forming gone",
                 "\\u274c <b>Setup gone</b>\\n\\n" + detail +
                 "\\n\\nWhat was forming has dropped away. "
                 "Nothing would fire if this candle closed now.", c)
        return'''
new = '''    if not rows:
        if prev:
            pts, _, pbody = prev.partition("@@")
            if not pbody:
                pbody = pts; pts = ""
            gone = ["\\u2022 " + x for x in pbody.split("|") if x.strip()]
            when = ""
            try:
                _t = datetime.fromisoformat(pts).replace(tzinfo=FEED_TZ)
                when = (" on the " + _t.astimezone(SAST).strftime("%H:%M")
                        + " candle (closed "
                        + (_t + timedelta(minutes=15)).astimezone(SAST).strftime("%H:%M")
                        + ")")
            except Exception:
                pass
            if not gone:
                return                      # nothing meaningful to report
            send("Forming gone",
                 "\\u274c <b>Setup gone</b>" + when + "\\n\\n"
                 + chr(10).join(gone) +
                 "\\n\\n<i>These were forming and dropped away before the close. "
                 "A signal alert for a different model on the same candle is not "
                 "a contradiction \\u2014 each model is judged on its own.</i>", c)
        return'''
if old in src:
    src = src.replace(old, new, 1)

# ---------- 4. forming display uses the same readable label -------------
old = '''        lines.append(f"{arrow} <b>{r['dir'].upper()}</b>   "
                     f"{nice(r['sym'])}\\n"
                     f"{mtag(r['model'])}{hit}\\n"
                     f"entry {r['entry']}   stop {r['stop']}")'''
new = '''        lines.append(f"{arrow} <b>{r['dir'].upper()}</b>   "
                     f"{nice(r['sym'])}\\n"
                     f"{mtag(r['model'])}{hit}\\n"
                     f"entry {r['entry']}   stop {r['stop']}")
        # (controls already filtered out above)'''
if old in src:
    src = src.replace(old, new, 1)

if src == orig:
    print("  nothing changed - alerts.py may already be patched")
else:
    open(p, "w").write(src)
    import ast
    ast.parse(src)
    print("  alerts.py patched:")
    print("   - controls excluded from forming alerts and conflict lines")
    print("   - forming labels now 'Model \\u00b7 Symbol \\u00b7 DIR'")
    print("   - 'Setup gone' names the candle it belonged to")
    print("   - empty 'gone' lists no longer send an alert")
