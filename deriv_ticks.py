#!/usr/bin/env python3
"""
deriv_ticks.py - pull tick history from Deriv's public WebSocket API.

Pure stdlib. No API token needed for tick history (public market data).
Implements a minimal RFC6455 client over ssl.socket because stdlib has no
websocket module.

Usage:
    python3 deriv_ticks.py --symbol R_75 --count 100000 --out r75_ticks.jsonl
    python3 deriv_ticks.py --list-symbols

Output format: one JSON object per line
    {"epoch": 1690000000, "quote": 375360.4635, "digit": 5}

Notes
-----
- R_75  = Volatility 75 Index  (2-second ticks)
- 1HZ75V = Volatility 75 (1s) Index (1-second ticks)
  The PDF screenshots show "Volatility 75 Index" -> R_75.
- The last digit is taken from the quote formatted to the symbol's pip
  precision, NOT from the raw float repr. 375360.4600 has last digit 0,
  and float repr would hide that. This matters: getting it wrong biases
  the digit distribution toward nonzero values and would manufacture a
  fake "edge".
"""

import argparse
import base64
import json
import os
import socket
import ssl
import struct
import sys
import time

HOST = "ws.derivws.com"
PATH = "/websockets/v3?app_id=1&l=EN&brand=deriv"
PORT = 443


# --------------------------------------------------------------------------
# Minimal WebSocket client (RFC 6455, text frames only)
# --------------------------------------------------------------------------

class WS:
    def __init__(self, host=HOST, port=PORT, path=PATH, timeout=30):
        raw = socket.create_connection((host, port), timeout=timeout)
        ctx = ssl.create_default_context()
        self.sock = ctx.wrap_socket(raw, server_hostname=host)
        self._handshake(host, path)

    def _handshake(self, host, path):
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Origin: https://app.deriv.com\r\n"
            "\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("connection closed during handshake")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode(errors="replace")
        if "101" not in status:
            raise ConnectionError(f"handshake failed: {status}")
        self._buf = rest

    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, payload, opcode=0x1):
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        key = os.urandom(4)
        header += key
        masked = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def send(self, obj):
        self._send_frame(json.dumps(obj).encode())

    def recv(self):
        """Return the next complete text message as a decoded dict."""
        frags = []
        frag_op = None
        while True:
            b0, b1 = self._recv_exact(2)
            fin = bool(b0 & 0x80)
            opcode = b0 & 0x0F
            masked = bool(b1 & 0x80)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            key = self._recv_exact(4) if masked else None
            payload = self._recv_exact(n) if n else b""
            if key:
                payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))

            if opcode == 0x9:            # ping -> pong
                self._send_frame(payload, opcode=0xA)
                continue
            if opcode == 0xA:            # pong
                continue
            if opcode == 0x8:            # close
                raise ConnectionError("server closed connection")
            if opcode == 0x0:            # continuation
                frags.append(payload)
            else:
                frag_op = opcode
                frags = [payload]
            if fin:
                data = b"".join(frags)
                if frag_op == 0x1:
                    return json.loads(data.decode())
                frags, frag_op = [], None

    def close(self):
        try:
            self._send_frame(b"", opcode=0x8)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Deriv API helpers
# --------------------------------------------------------------------------

def request(ws, payload, expect, retries=3):
    """Send a request and wait for the matching msg_type. Raises on API error."""
    for attempt in range(retries):
        ws.send(payload)
        deadline = time.time() + 30
        while time.time() < deadline:
            msg = ws.recv()
            if "error" in msg:
                raise RuntimeError(
                    f"Deriv API error {msg['error'].get('code')}: "
                    f"{msg['error'].get('message')}"
                )
            if msg.get("msg_type") == expect:
                return msg
        if attempt < retries - 1:
            time.sleep(2)
    raise TimeoutError(f"no {expect} response after {retries} attempts")


def get_pip_decimals(ws, symbol):
    """Decimal places for the symbol, from Deriv's own metadata."""
    msg = request(ws, {"active_symbols": "brief", "product_type": "basic"},
                  "active_symbols")
    for s in msg["active_symbols"]:
        if s["symbol"] == symbol:
            pip = float(s["pip"])
            dec = 0
            while pip < 1 - 1e-12:
                pip *= 10
                dec += 1
            return dec
    raise ValueError(f"symbol {symbol} not found in active_symbols")


def list_symbols(ws):
    msg = request(ws, {"active_symbols": "brief", "product_type": "basic"},
                  "active_symbols")
    rows = [s for s in msg["active_symbols"]
            if "synthetic" in s.get("market", "").lower()
            or "Volatility" in s.get("display_name", "")]
    rows.sort(key=lambda s: s["symbol"])
    for s in rows:
        print(f"{s['symbol']:<12} pip={s['pip']:<10} {s['display_name']}")


def last_digit(quote, decimals):
    return int(f"{quote:.{decimals}f}"[-1])


def collect(symbol, target, out_path, chunk=5000):
    """
    Page backwards through history until `target` ticks are collected.
    Deriv caps ticks_history at 5000 per request.
    """
    ws = WS()
    try:
        decimals = get_pip_decimals(ws, symbol)
        print(f"symbol={symbol} decimals={decimals} target={target}", file=sys.stderr)

        rows = {}          # epoch -> (quote, digit); dedupes overlap between pages
        end = "latest"
        stalls = 0

        while len(rows) < target:
            msg = request(ws, {
                "ticks_history": symbol,
                "end": end,
                "count": min(chunk, target - len(rows) + 10),
                "style": "ticks",
            }, "history")

            hist = msg.get("history", {})
            prices = hist.get("prices", [])
            times = hist.get("times", [])
            if not prices:
                print("empty page; stopping", file=sys.stderr)
                break

            before = len(rows)
            for t, p in zip(times, prices):
                q = float(p)
                rows[int(t)] = (q, last_digit(q, decimals))
            gained = len(rows) - before

            print(f"  +{gained:>5} (total {len(rows)})", file=sys.stderr)

            if gained == 0:
                stalls += 1
                if stalls >= 2:
                    print("no new data; history exhausted", file=sys.stderr)
                    break
            else:
                stalls = 0

            end = int(min(times)) - 1
            time.sleep(0.6)          # be polite to the public endpoint

        with open(out_path, "w") as f:
            for epoch in sorted(rows):
                q, d = rows[epoch]
                f.write(json.dumps({"epoch": epoch, "quote": q, "digit": d}) + "\n")

        print(f"wrote {len(rows)} ticks -> {out_path}", file=sys.stderr)
        return len(rows)
    finally:
        ws.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="R_75")
    ap.add_argument("--count", type=int, default=100000)
    ap.add_argument("--out", default="ticks.jsonl")
    ap.add_argument("--list-symbols", action="store_true")
    a = ap.parse_args()

    if a.list_symbols:
        ws = WS()
        try:
            list_symbols(ws)
        finally:
            ws.close()
        return

    collect(a.symbol, a.count, a.out)


if __name__ == "__main__":
    main()
