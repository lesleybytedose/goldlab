#!/usr/bin/env python3
"""One-shot: add the volume column to every existing bar CSV.
Old rows get volume 0. Run ONCE with the ingest service STOPPED."""
import glob, os
RAW = os.path.expanduser("~/goldlab/data/raw")
for p in sorted(glob.glob(os.path.join(RAW, "*_M15_live.csv"))):
    lines = open(p).read().splitlines()
    if not lines:
        continue
    if lines[0].strip().endswith(",volume"):
        print(f"  {os.path.basename(p)}: already migrated"); continue
    out = ["date,time,open,high,low,close,spread,volume"]
    fixed = 0
    for ln in lines[1:]:
        if not ln.strip():
            continue
        n = ln.count(",")
        if n == 6:
            out.append(ln + ",0"); fixed += 1
        elif n == 7:
            out.append(ln)
        # anything else is a corrupt row: drop it
    tmp = p + ".tmp"
    open(tmp, "w").write("\n".join(out) + "\n")
    os.replace(tmp, p)
    print(f"  {os.path.basename(p)}: {fixed} rows migrated, {len(out)-1} total")
