#!/usr/bin/env python3
"""
deriv_specs.py - ask Deriv directly what it offers on the spike indices.

The multiplier finding lives or dies on one fact: is x1000 available on
Boom/Crash? Everything at x500 and below failed once the overlapping-window
correction was applied. This queries contracts_for (public, no token) and
prints:

  - every MULTIPLIER tier offered per symbol, with the stop-out distance
    and whether a typical spike exceeds it
  - ACCUMULATOR availability and growth rates
  - commission, min/max stake, and any cancellation option

No account needed. Read-only.
"""
import json, os, ssl, sys

SYMS = ["BOOM1000", "CRASH1000", "BOOM500", "CRASH500",
        "R_75", "R_100", "1HZ100V"]
WS = "wss://ws.derivws.com/websockets/v3?app_id=1089"
# measured median spike as % of price, from our own tick data
SPIKE = {"BOOM1000": 0.1055, "CRASH1000": 0.0988,
         "BOOM500": 0.0965, "CRASH500": 0.0839}


def ask(ws, payload):
    ws.send(json.dumps(payload))
    return json.loads(ws.recv())


def main():
    from websocket import create_connection
    ws = create_connection(WS, timeout=30)
    try:
        for sym in SYMS:
            r = ask(ws, {"contracts_for": sym, "currency": "USD"})
            if "error" in r:
                print("\n" + sym + ": " + str(r["error"].get("message")))
                continue
            av = (r.get("contracts_for") or {}).get("available") or []
            mults, accs = set(), set()
            info = {}
            for c in av:
                cat = str(c.get("contract_category") or "")
                if "multiplier" in cat.lower() or c.get("contract_type") in ("MULTUP", "MULTDOWN"):
                    for m in (c.get("multiplier_range") or []):
                        mults.add(int(m))
                    if c.get("multiplier_range"):
                        info["cancel"] = c.get("cancellation_range")
                        info["min"] = c.get("min_stake")
                        info["max"] = c.get("max_stake")
                if c.get("contract_type") == "ACCU" or "accumulator" in cat.lower():
                    for g in (c.get("growth_rate_range") or []):
                        accs.add(float(g))
                    info["acc_max_ticks"] = c.get("max_ticks")
                    info["acc_payout_cap"] = c.get("maximum_payout")
            print("\n" + "=" * 60)
            print(sym)
            if mults:
                ms = sorted(mults)
                print("  MULTIPLIERS offered: " + ", ".join("x" + str(m) for m in ms))
                sp = SPIKE.get(sym)
                if sp:
                    print("  spike = " + str(sp) + "% of price")
                    print("    tier    stop-out   spike gaps through?")
                    for m in ms:
                        cut = 100.0 / m
                        print("    x" + str(m).ljust(6) + ("%.3f%%" % cut).rjust(9)
                              + ("   YES" if sp > cut else "   no"))
                    usable = [m for m in ms if sp > 100.0/m]
                    print("  => tiers where the loss cap can bite: "
                          + (", ".join("x" + str(m) for m in usable) if usable
                             else "NONE - the effect cannot exist here"))
                if info.get("cancel"):
                    print("  deal cancellation: " + str(info["cancel"]))
                if info.get("min"):
                    print("  stake range: " + str(info.get("min")) + " - "
                          + str(info.get("max")))
            else:
                print("  no multipliers offered")
            if accs:
                print("  ACCUMULATORS: growth rates "
                      + ", ".join(str(int(g*100)) + "%" for g in sorted(accs))
                      + "   max ticks " + str(info.get("acc_max_ticks"))
                      + "   payout cap " + str(info.get("acc_payout_cap")))
            else:
                print("  no accumulators offered")
    finally:
        ws.close()


main()
