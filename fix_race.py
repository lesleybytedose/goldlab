#!/usr/bin/env python3
"""
fix_race.py - remove the race between forming, signals and the cron.

The problem: alerts.py runs check_forming BEFORE check_signals, and live.py
logs signals on a SEPARATE cron. So when a candle closes, forming.json
empties before live.py has written the signal - and the "gone" alert fires
against a signal log that does not know yet. Checking the log is not enough;
the check itself can run too early.

The fix is semantic. "Setup gone" only means something while the candle is
STILL OPEN - it is a warning that what you were watching has died. Once the
candle closes the outcome is binary: it fired (the signal alert says so) or
it did not (nothing worth sending). So:

  - no "gone" alert is ever sent for a candle that has already closed
  - check_signals now runs BEFORE check_forming, so a signal always
    reaches you first
"""
import os

p = os.path.expanduser("~/goldlab/alerts.py")
src = open(p).read()
orig = src

# ---- 1. suppress "gone" once the candle has closed ---------------------
old = '''            # which of them actually FIRED on that candle?'''
new = '''            # A closed candle's outcome belongs to the signal path, not here.
            # Only warn about a setup dying while its candle is still open.
            try:
                _pt = datetime.fromisoformat(pts).replace(tzinfo=FEED_TZ)
                if datetime.now(FEED_TZ) >= _pt + timedelta(minutes=15):
                    return
            except Exception:
                return          # cannot establish the candle: say nothing

            # which of them actually FIRED on that candle?'''
if old in src:
    src = src.replace(old, new, 1)

# ---- 2. signals go out before forming chatter --------------------------
old = '''    check_forming(c, known, fresh)
    check_approach(c, known, fresh)
    check_feed(c, known, fresh)
    check_signals(c, known, fresh)
    check_fills(c, known, fresh)
    check_news(c, known, fresh)'''
new = '''    # signals first: a confirmed setup must always reach you before any
    # commentary about setups that did not survive
    check_signals(c, known, fresh)
    check_forming(c, known, fresh)
    check_approach(c, known, fresh)
    check_feed(c, known, fresh)
    check_fills(c, known, fresh)
    check_news(c, known, fresh)'''
if old in src:
    src = src.replace(old, new, 1)

if src == orig:
    print("  nothing changed - already patched or alerts.py differs")
else:
    open(p, "w").write(src)
    import ast; ast.parse(src)
    print("  alerts.py patched:")
    print("   - no 'Setup gone' for a candle that has already closed")
    print("   - signals are checked and sent BEFORE forming commentary")
    print("   - the fired-check from the previous patch stays as a backstop")
