#!/usr/bin/env python3
"""PocketDeck - use your phone as your laptop's touchpad.

Nothing to install on the phone: this program serves a touchpad web page.
Connect the phone to the laptop over Bluetooth tethering (or the same Wi-Fi),
open the printed address in the phone's browser, and use it as a touchpad.

Works on Windows and Linux (Wayland + X11). Python 3.8+, standard library only.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
import secrets
import socket
import struct
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs

from backends import make_injector

HERE = Path(__file__).resolve().parent
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
BUTTONS = {"left", "right", "middle"}

# --------------------------------------------------------------------------
# Gestures -> keyboard shortcuts (override in config.json, see README)
# --------------------------------------------------------------------------
GESTURES = {
    "windows": {
        "swipe3_up": ["super", "tab"], "swipe3_down": ["super", "d"],
        "swipe3_left": ["alt", "tab"], "swipe3_right": ["alt", "tab"],
        "swipe4_up": ["super", "tab"], "swipe4_down": ["super", "d"],
        "swipe4_left": ["ctrl", "super", "right"], "swipe4_right": ["ctrl", "super", "left"],
    },
    "kde": {
        "swipe3_up": ["super", "w"], "swipe3_down": ["super", "d"],
        "swipe3_left": ["alt", "tab"], "swipe3_right": ["alt", "tab"],
        "swipe4_up": ["super", "w"], "swipe4_down": ["super", "d"],
        "swipe4_left": ["ctrl", "super", "right"], "swipe4_right": ["ctrl", "super", "left"],
    },
    "gnome": {
        "swipe3_up": ["super"], "swipe3_down": ["super"],
        "swipe3_left": ["alt", "tab"], "swipe3_right": ["alt", "tab"],
        "swipe4_up": ["super"], "swipe4_down": ["super"],
        "swipe4_left": ["super", "pagedown"], "swipe4_right": ["super", "pageup"],
    },
}


def detect_desktop():
    if sys.platform == "win32":
        return "windows"
    de = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    return "gnome" if "gnome" in de else "kde"


def load_config():
    path = HERE / "config.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            print(f"Warning: config.json is invalid ({e}); using defaults")
    return {}


def settings_file():
    base = os.environ.get("APPDATA") if sys.platform == "win32" else (
        os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))
    new = Path(base) / "pocketdeck" / "settings.json"
    old = Path(base) / "mobilepad" / "settings.json"  # name used by earlier versions
    if not new.exists() and old.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        old.replace(new)
    return new


def load_or_create_pin(forced=None):
    f = settings_file()
    data = {}
    if f.exists():
        try:
            data = json.loads(f.read_text())
        except ValueError:
            pass
    if forced:
        data["pin"] = forced
    if not data.get("pin"):
        data["pin"] = f"{secrets.randbelow(10**6):06d}"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data))
    return data["pin"]


# --------------------------------------------------------------------------
# Network addresses (marks the Bluetooth one)
# --------------------------------------------------------------------------
def local_addresses():
    """Return list of (label, ip, is_bluetooth)."""
    out = []
    try:
        if sys.platform == "win32":
            text = subprocess.run(["ipconfig"], capture_output=True, text=True,
                                  errors="replace").stdout
            adapter = ""
            for line in text.splitlines():
                if line and not line[0].isspace() and line.rstrip().endswith(":"):
                    adapter = line.strip().rstrip(":")
                elif "IPv4" in line and ":" in line:
                    ip = line.rsplit(":", 1)[1].strip().split("(")[0].strip()
                    out.append((adapter, ip, "bluetooth" in adapter.lower()))
        else:
            text = subprocess.run(["ip", "-4", "-o", "addr", "show"],
                                  capture_output=True, text=True).stdout
            for line in text.splitlines():
                parts = line.split()
                iface, ip = parts[1], parts[3].split("/")[0]
                bt = iface.startswith(("bnep", "bt", "pan"))
                out.append((iface, ip, bt))
    except (OSError, IndexError):
        pass
    if not out:
        try:
            for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
                out.append(("network", ip, False))
        except OSError:
            pass
    return [a for a in out if not a[1].startswith("127.")]


def print_addresses(addrs, port):
    for label, ip, bt in sorted(addrs, key=lambda a: not a[2]):
        tag = "BLUETOOTH" if bt else "Wi-Fi/LAN"
        print(f"   [{tag:9}]  http://{ip}:{port}      ({label})")


# --------------------------------------------------------------------------
# Minimal WebSocket (RFC 6455) on asyncio streams
# --------------------------------------------------------------------------
async def ws_recv(reader):
    b1, b2 = await reader.readexactly(2)
    opcode, masked, n = b1 & 0x0F, b2 & 0x80, b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", await reader.readexactly(8))[0]
    if n > 1 << 16:
        raise ConnectionError("frame too large")
    mask = await reader.readexactly(4) if masked else None
    data = await reader.readexactly(n)
    if mask:
        data = bytes(b ^ mask[i & 3] for i, b in enumerate(data))
    return opcode, data


def ws_frame(opcode, payload=b""):
    n = len(payload)
    head = bytes([0x80 | opcode])
    head += bytes([n]) if n < 126 else bytes([126]) + struct.pack("!H", n)
    return head + payload


class Server:
    def __init__(self, injector, pin, gestures, port):
        self.inj = injector
        self.pin = pin
        self.gestures = gestures
        self.port = port
        self.zoom_acc = 0.0

    async def handle(self, reader, writer):
        sock = writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = raw.decode("latin-1").split("\r\n")
            _method, target, _ = lines[0].split(" ", 2)
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            path, _, query = target.partition("?")
            if path == "/ws" and headers.get("upgrade", "").lower() == "websocket":
                await self.websocket(reader, writer, headers, parse_qs(query))
            elif path in ("/", "/index.html"):
                body = (HERE / "web" / "index.html").read_bytes()
                self.http(writer, "200 OK", body, "text/html; charset=utf-8")
            else:
                self.http(writer, "404 Not Found", b"Not found", "text/plain")
            await writer.drain()
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError,
                ValueError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()

    @staticmethod
    def http(writer, status, body, ctype):
        writer.write((f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\n"
                      f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
                      "Connection: close\r\n\r\n").encode() + body)

    async def websocket(self, reader, writer, headers, params):
        key = headers.get("sec-websocket-key", "")
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        peer = writer.get_extra_info("peername")
        peer_ip = peer[0] if peer else "?"

        if not secrets.compare_digest(params.get("k", [""])[0], self.pin):
            await asyncio.sleep(1)  # slow down PIN guessing
            writer.write(ws_frame(8, struct.pack("!H", 4001) + b"bad pin"))
            await writer.drain()
            print(f"  ! Rejected {peer_ip} (wrong PIN)")
            return

        writer.write(ws_frame(1, json.dumps(
            {"t": "hello", "host": socket.gethostname()}).encode()))
        await writer.drain()
        print(f"  + Phone connected: {peer_ip}")
        try:
            while True:
                opcode, data = await ws_recv(reader)
                if opcode == 8:
                    break
                if opcode == 9:
                    writer.write(ws_frame(10, data))
                elif opcode == 1:
                    try:
                        self.command(json.loads(data))
                    except (ValueError, TypeError, KeyError, IndexError):
                        pass
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self.inj.release_all()
            print(f"  - Phone disconnected: {peer_ip}")

    def command(self, msg):
        op = msg[0]
        clamp = lambda v, lim: max(-lim, min(lim, float(v)))
        if op == "m":
            self.inj.move(clamp(msg[1], 3000), clamp(msg[2], 3000))
        elif op == "s":
            self.inj.scroll(clamp(msg[1], 50), clamp(msg[2], 50))
        elif op in ("d", "u", "c") and msg[1] in BUTTONS:
            if op == "c":
                self.inj.click(msg[1], int(clamp(msg[2], 3)) if len(msg) > 2 else 1)
            else:
                self.inj.button(msg[1], op == "d")
        elif op == "z":
            self.zoom_acc += clamp(msg[1], 20)
            n = int(self.zoom_acc)
            if n:
                self.zoom_acc -= n
                self.inj.zoom(n)
        elif op == "g":
            combo = self.gestures.get(str(msg[1]))
            if combo:
                self.inj.keys(combo)
        elif op == "k" and isinstance(msg[1], list):
            self.inj.keys([str(k) for k in msg[1]][:5])
        elif op == "t":
            self.inj.type_text(str(msg[1])[:1000])
        elif op == "bs":
            self.inj.tap("backspace", int(clamp(msg[1], 500)))


async def watch_addresses(port, known):
    """Print new addresses as they appear (e.g. when Bluetooth tethering connects)."""
    while True:
        await asyncio.sleep(4)
        now = local_addresses()
        new = [a for a in now if a[1] not in known]
        if new:
            print("\n  New network detected - open on your phone:")
            print_addresses(new, port)
            known.update(a[1] for a in new)


async def main():
    ap = argparse.ArgumentParser(description="Use your phone as a laptop touchpad.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--pin", help="set a new PIN (it is remembered)")
    args = ap.parse_args()

    config = load_config()
    desktop = config.get("desktop") or detect_desktop()
    gestures = dict(GESTURES.get(desktop, GESTURES["kde"]))
    gestures.update(config.get("gestures", {}))
    pin = load_or_create_pin(args.pin)

    injector = make_injector()
    server = Server(injector, pin, gestures, args.port)
    srv = await asyncio.start_server(server.handle, args.host, args.port)

    addrs = local_addresses()
    print("=" * 60)
    print(f" PocketDeck running   (input: {injector.name}, gestures: {desktop})")
    print(f" PIN: {pin}")
    print("=" * 60)
    print(" Open this address in your phone's browser:")
    print_addresses(addrs, args.port)
    if not any(a[2] for a in addrs):
        print("\n No Bluetooth network yet. Turn on Bluetooth tethering on")
        print(" your phone and connect the laptop to it (see README.md).")
    print("\n Press Ctrl+C to stop.\n")

    try:
        async with srv:
            await asyncio.gather(srv.serve_forever(),
                                 watch_addresses(args.port, {a[1] for a in addrs}))
    finally:
        injector.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
