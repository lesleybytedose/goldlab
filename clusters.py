#!/usr/bin/env python3
"""
clusters.py - how many independent level rules sit at a given price.

Used by live.py to stamp every signal (models AND controls) with the
level context at its entry, so the question "do signals at multi-rule
levels resolve better?" becomes measurable instead of arguable.

STRICT NO-LOOKAHEAD: every level is built from bars with index <= i only.
The signal bar's own high/low are excluded from swing detection, because
a swing needs bars after it to exist and those had not closed yet.

    from clusters import cluster_at
    c = cluster_at(bars, i, atr_value)
    # -> {"lvl_n": 3, "lvl_src": "pivot S1|prior day low|H1 swing low",
    #     "lvl_dist_atr": 0.12}

lvl_n counts distinct RULES within MERGE_ATR of the entry price. It is a
covariate for later analysis, not a filter. Nothing acts on it.
"""
MERGE_ATR = 0.25


def _swing_levels(b, i, step, k=2, back=60):
    """Swing highs/lows on a step-aggregated view, using closed bars only."""
    out = []
    idxs = list(range(max(0, i - back * step), i + 1, step))
    if len(idxs) < 2 * k + 3:
        return out
    cands = []
    for a in range(len(idxs) - 1):
        lo_i, hi_i = idxs[a], idxs[a + 1]
        seg = b[lo_i:hi_i]
        if not seg:
            continue
        cands.append((max(x[2] for x in seg), min(x[3] for x in seg)))
    # a swing needs k candles either side; the last k are not yet confirmed
    for a in range(k, len(cands) - k):
        hs = [cands[j][0] for j in range(a - k, a + k + 1)]
        ls = [cands[j][1] for j in range(a - k, a + k + 1)]
        if cands[a][0] == max(hs):
            out.append((cands[a][0], "swing high"))
        if cands[a][1] == min(ls):
            out.append((cands[a][1], "swing low"))
    return out


def levels_at(b, i):
    """All rule-based levels visible at bar i. Returns [(price, source)]."""
    L = []
    day = b[i][0].date()
    # walk back to find the prior session and today's bars so far
    today, prev = [], []
    j = i
    while j >= 0 and b[j][0].date() == day:
        today.append(b[j]); j -= 1
    if j >= 0:
        pday = b[j][0].date()
        while j >= 0 and b[j][0].date() == pday:
            prev.append(b[j]); j -= 1
    today.reverse(); prev.reverse()

    if prev:
        yh = max(x[2] for x in prev); yl = min(x[3] for x in prev); yc = prev[-1][4]
        L += [(yh, "prior day high"), (yl, "prior day low"), (yc, "prior day close")]
        p = (yh + yl + yc) / 3.0; rng = yh - yl
        L += [(p, "pivot P"), (2*p - yl, "pivot R1"), (2*p - yh, "pivot S1"),
              (p + rng, "pivot R2"), (p - rng, "pivot S2")]
    if len(today) >= 2:
        # exclude the signal bar itself from "today's range so far"
        t = today[:-1]
        L += [(max(x[2] for x in t), "today high"), (min(x[3] for x in t), "today low")]
        if len(t) >= 8:
            L += [(max(x[2] for x in t[:8]), "opening range high"),
                  (min(x[3] for x in t[:8]), "opening range low")]
    if i >= 21:
        L += [(max(x[2] for x in b[i-20:i]), "donchian 20 high"),
              (min(x[3] for x in b[i-20:i]), "donchian 20 low")]
    for step, tag in ((4, "H1"), (16, "H4")):
        for px, kind in _swing_levels(b, i, step):
            L.append((px, f"{tag} {kind}"))
    px = b[i][4]
    st = (500.0 if px > 10000 else 50.0 if px > 1000 else
          5.0 if px > 100 else 1.0 if px > 10 else 0.1)
    L += [((px // st) * st, "round number"), ((px // st) * st + st, "round number")]
    return L


def cluster_at(b, i, atr_val, price=None):
    """Rules within MERGE_ATR of the entry price. Cheap, no lookahead."""
    try:
        if atr_val is None or atr_val <= 0:
            return {}
        p = b[i][4] if price is None else price
        tol = MERGE_ATR * atr_val
        hits = {}
        for lv, src in levels_at(b, i):
            d = abs(lv - p)
            if d <= tol:
                if src not in hits or d < hits[src]:
                    hits[src] = d
        if not hits:
            return {"lvl_n": 0, "lvl_src": "", "lvl_dist_atr": None}
        near = min(hits.values()) / atr_val
        return {"lvl_n": len(hits),
                "lvl_src": "|".join(sorted(hits)),
                "lvl_dist_atr": round(near, 3)}
    except Exception:
        return {}


def headroom_at(b, i, atr_val, entry, target, direction):
    """Is there a level cluster BETWEEN the entry and the target?

    Pure geometry. Says nothing about whether the level will hold - only
    that the trade has to pass through it to reach its target, and how
    many independent rules mark that price.

        hr_atr    distance from entry to the nearest opposing cluster, in ATR
        hr_n      how many rules mark that cluster
        hr_src    which rules
        tp_blocked True if the target sits BEYOND that cluster
        headroom_r how far the nearest obstacle is, expressed in R
    """
    try:
        if not atr_val or atr_val <= 0:
            return {}
        tol = MERGE_ATR * atr_val
        up = direction == "long"
        # group levels lying between entry and target
        groups = []
        for lv, src in sorted(levels_at(b, i)):
            if up and not (entry < lv <= max(target, entry)):
                continue
            if (not up) and not (min(target, entry) <= lv < entry):
                continue
            if groups and abs(lv - groups[-1]["price"]) <= tol:
                groups[-1]["srcs"].add(src)
            else:
                groups.append({"price": lv, "srcs": {src}})
        if not groups:
            return {"hr_n": 0, "hr_atr": None, "hr_src": "", "tp_blocked": False}
        nearest = min(groups, key=lambda g: abs(g["price"] - entry))
        # the "obstacle" worth naming is the densest one in the path
        dense = max(groups, key=lambda g: len(g["srcs"]))
        d = abs(nearest["price"] - entry)
        return {"hr_n": len(dense["srcs"]),
                "hr_atr": round(d / atr_val, 2),
                "hr_src": "|".join(sorted(dense["srcs"]))[:120],
                "tp_blocked": True}
    except Exception:
        return {}


def level_state(b, i, atr_val, direction, back=40):
    """Is price sitting AT a level right now, and has it been rejected there?

    Different question from headroom_at(). Headroom asks what stands between
    entry and target. This asks what price is pressed against at the moment
    of entry, and how many times it has already failed to get through.

        at_level      True if entry is within MERGE_ATR of a cluster
        lvl_side      "resistance" (cluster above) or "support" (below)
        lvl_touches   bars in the last `back` that reached that cluster
        lvl_bars_at   consecutive recent bars within tolerance of it
        into_level    True if the trade is heading INTO it (long under
                      resistance, short above support)

    No lookahead: bars > i are never read.
    """
    try:
        if not atr_val or atr_val <= 0 or i < 5:
            return {}
        p = b[i][4]
        tol = MERGE_ATR * atr_val
        # cluster the visible levels, keep the one nearest price
        groups = []
        for lv, src in sorted(levels_at(b, i)):
            if groups and abs(lv - groups[-1]["price"]) <= tol:
                groups[-1]["srcs"].add(src)
            else:
                groups.append({"price": lv, "srcs": {src}})
        if not groups:
            return {}
        near = min(groups, key=lambda g: abs(g["price"] - p))
        d = near["price"] - p
        if abs(d) > tol:
            return {"at_level": False}
        side = "resistance" if d >= 0 else "support"
        lo = max(0, i - back)
        touches = sum(1 for k in range(lo, i + 1)
                      if b[k][2] >= near["price"] - tol
                      and b[k][3] <= near["price"] + tol)
        bars_at = 0
        for k in range(i, lo - 1, -1):
            if abs(b[k][4] - near["price"]) <= tol:
                bars_at += 1
            else:
                break
        into = (direction == "long" and side == "resistance") or \
               (direction == "short" and side == "support")
        return {"at_level": True, "lvl_side": side,
                "lvl_touches": touches, "lvl_bars_at": bars_at,
                "lvl_rules": len(near["srcs"]),
                "into_level": into}
    except Exception:
        return {}
