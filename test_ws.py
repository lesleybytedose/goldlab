import json, struct, os
import deriv_ticks as dt

class FakeSock:
    """Loopback: frames written by the client get unmasked and echoed back
    unmasked, the way a real server would send them."""
    def __init__(self): self.out = b""; self.inb = b""
    def sendall(self, b): self.out += b
    def recv(self, n):
        chunk, self.inb = self.inb[:n], self.inb[n:]
        if not chunk: raise ConnectionError("empty")
        return chunk
    def close(self): pass

def server_frame(payload, opcode=0x1, fin=True):
    h = bytearray([(0x80 if fin else 0)|opcode]); n=len(payload)
    if n<126: h.append(n)
    elif n<65536: h.append(126); h+=struct.pack(">H",n)
    else: h.append(127); h+=struct.pack(">Q",n)
    return bytes(h)+payload

ws = dt.WS.__new__(dt.WS)
ws.sock = FakeSock(); ws._buf = b""

# 1. client masking round-trip
msg = {"ticks_history":"R_75","end":"latest","count":5000,"style":"ticks"}
ws.send(msg)
raw = ws.sock.out
assert raw[0]==0x81 and (raw[1]&0x80), "must set FIN|text and MASK bit"
ln = raw[1]&0x7F; off=2
if ln==126: ln=struct.unpack(">H",raw[2:4])[0]; off=4
key=raw[off:off+4]; body=raw[off+4:]
un = bytes(b ^ key[i%4] for i,b in enumerate(body))
assert json.loads(un.decode())==msg, "unmask round-trip failed"
print("PASS  client frame masking")

# 2. small server frame
ws.sock.inb = server_frame(json.dumps({"msg_type":"history"}).encode())
assert ws.recv()["msg_type"]=="history"
print("PASS  small server frame")

# 3. large frame (>64KB, 8-byte length) - a 5000-tick page is ~150KB
big = {"msg_type":"history","history":{"prices":[375000.1234]*5000,"times":list(range(5000))}}
ws.sock.inb = server_frame(json.dumps(big).encode())
r = ws.recv(); assert len(r["history"]["prices"])==5000
print("PASS  large frame, 8-byte length")

# 4. ping mid-stream must be answered with a pong, then message delivered
ws.sock.out = b""
ws.sock.inb = server_frame(b"hb", opcode=0x9) + server_frame(json.dumps({"msg_type":"history"}).encode())
assert ws.recv()["msg_type"]=="history"
assert ws.sock.out and (ws.sock.out[0]&0x0F)==0xA, "should have replied pong"
print("PASS  ping/pong handling")

# 5. fragmented message
p = json.dumps({"msg_type":"history","x":1}).encode()
ws.sock.inb = server_frame(p[:10],opcode=0x1,fin=False)+server_frame(p[10:],opcode=0x0,fin=True)
assert ws.recv()["x"]==1
print("PASS  fragmented message reassembly")

# 6. last-digit extraction must respect trailing zeros
for q,dec,exp in [(375360.4635,4,5),(375360.4600,4,0),(375360.4630,4,0),(1234.50,2,0),(1234.57,2,7)]:
    got = dt.last_digit(q,dec)
    assert got==exp, f"{q} @{dec}dp -> {got}, expected {exp}"
print("PASS  last-digit extraction incl. trailing zeros")
