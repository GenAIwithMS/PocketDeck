"""Linux input backend.

Creates a virtual mouse and a virtual keyboard through /dev/uinput, so it
works on Wayland and X11 alike. Pure standard library (no python-evdev).
"""
import fcntl
import os
import struct
import subprocess
import time

# ioctl numbers from <linux/uinput.h>
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_RELBIT = 0x40045566
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502

EV_SYN, EV_KEY, EV_REL = 0x00, 0x01, 0x02
SYN_REPORT = 0
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0x00, 0x01, 0x06, 0x08
REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES = 0x0B, 0x0C
BUS_VIRTUAL = 0x06

BUTTONS = {"left": 0x110, "right": 0x111, "middle": 0x112}

KEYS = {
    "esc": 1, "backspace": 14, "tab": 15, "enter": 28, "ctrl": 29,
    "shift": 42, "alt": 56, "space": 57, "super": 125, "capslock": 58,
    "up": 103, "pageup": 104, "left": 105, "right": 106, "down": 108,
    "pagedown": 109, "home": 102, "end": 107, "insert": 110, "delete": 111,
    "printscreen": 99, "menu": 127,
    "mute": 113, "volumedown": 114, "volumeup": 115,
    "nextsong": 163, "playpause": 164, "previoussong": 165,
    "minus": 12, "equal": 13, "leftbrace": 26, "rightbrace": 27,
    "semicolon": 39, "apostrophe": 40, "grave": 41, "backslash": 43,
    "comma": 51, "dot": 52, "slash": 53,
}
KEYS.update({c: k for c, k in zip("qwertyuiop", range(16, 26))})
KEYS.update({c: k for c, k in zip("asdfghjkl", range(30, 39))})
KEYS.update({c: k for c, k in zip("zxcvbnm", range(44, 51))})
KEYS.update({c: k for c, k in zip("1234567890", range(2, 12))})
KEYS.update({f"f{i}": 58 + i for i in range(1, 11)})
KEYS.update({"f11": 87, "f12": 88})

# character -> (key name, needs shift) for a US keyboard layout
_PUNCT = {"-": "minus", "=": "equal", "[": "leftbrace", "]": "rightbrace",
          ";": "semicolon", "'": "apostrophe", "`": "grave", "\\": "backslash",
          ",": "comma", ".": "dot", "/": "slash", " ": "space",
          "\n": "enter", "\t": "tab"}
_SHIFTED = dict(zip('!@#$%^&*()_+{}:"~|<>?', "1234567890-=[];'`\\,./"))
CHARS = {}
for _c in "abcdefghijklmnopqrstuvwxyz0123456789":
    CHARS[_c] = (_c, False)
for _c in "abcdefghijklmnopqrstuvwxyz":
    CHARS[_c.upper()] = (_c, True)
for _c, _k in _PUNCT.items():
    CHARS[_c] = (_k, False)
for _c, _base in _SHIFTED.items():
    CHARS[_c] = (CHARS[_base][0], True)

_EVENT = struct.Struct("llHHi")           # struct input_event
_USER_DEV = struct.Struct("80sHHHHi256i")  # struct uinput_user_dev


class _Device:
    def __init__(self, name, keys, rels=()):
        try:
            self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        except FileNotFoundError:
            raise SystemExit("/dev/uinput not found. Run: sudo modprobe uinput")
        except PermissionError:
            raise SystemExit(
                "No permission to use /dev/uinput.\n"
                "Fix (one time):\n"
                "  echo 'KERNEL==\"uinput\", GROUP=\"input\", MODE=\"0660\"' | "
                "sudo tee /etc/udev/rules.d/99-pocketdeck.rules\n"
                "  sudo usermod -aG input $USER   # then log out and back in")
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        for k in keys:
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, k)
        if rels:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REL)
            for r in rels:
                fcntl.ioctl(self.fd, UI_SET_RELBIT, r)
        os.write(self.fd, _USER_DEV.pack(
            name.encode(), BUS_VIRTUAL, 0x1209, 0x5050, 1, 0, *([0] * 256)))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)

    def emit(self, etype, code, value):
        os.write(self.fd, _EVENT.pack(0, 0, etype, code, value))

    def syn(self):
        self.emit(EV_SYN, SYN_REPORT, 0)

    def close(self):
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        finally:
            os.close(self.fd)


class Injector:
    name = "Linux uinput"

    def __init__(self):
        self.mouse = _Device(
            "PocketDeck Pointer", BUTTONS.values(),
            (REL_X, REL_Y, REL_WHEEL, REL_HWHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES))
        self.kbd = _Device("PocketDeck Keyboard", KEYS.values())
        self._rx = self._ry = 0.0     # sub-pixel remainders
        self._hv = self._hh = 0.0     # hi-res scroll remainders
        self._lv = self._lh = 0       # legacy wheel accumulators (hi-res units)
        self._held_buttons = set()
        self._held_keys = set()
        time.sleep(0.4)  # let the compositor pick up the new devices

    def move(self, dx, dy):
        self._rx += dx
        self._ry += dy
        ix, iy = int(self._rx), int(self._ry)
        if not ix and not iy:
            return
        self._rx -= ix
        self._ry -= iy
        if ix:
            self.mouse.emit(EV_REL, REL_X, ix)
        if iy:
            self.mouse.emit(EV_REL, REL_Y, iy)
        self.mouse.syn()

    def scroll(self, h, v):
        """h/v in wheel notches (fractions allowed). +v = up, +h = right."""
        self._hv += v * 120
        self._hh += h * 120
        iv, ih = int(self._hv), int(self._hh)
        self._hv -= iv
        self._hh -= ih
        if not iv and not ih:
            return
        if iv:
            self.mouse.emit(EV_REL, REL_WHEEL_HI_RES, iv)
            self._lv += iv
            n = int(self._lv / 120)
            if n:
                self.mouse.emit(EV_REL, REL_WHEEL, n)
                self._lv -= n * 120
        if ih:
            self.mouse.emit(EV_REL, REL_HWHEEL_HI_RES, ih)
            self._lh += ih
            n = int(self._lh / 120)
            if n:
                self.mouse.emit(EV_REL, REL_HWHEEL, n)
                self._lh -= n * 120
        self.mouse.syn()

    def button(self, name, down):
        self.mouse.emit(EV_KEY, BUTTONS[name], 1 if down else 0)
        self.mouse.syn()
        (self._held_buttons.add if down else self._held_buttons.discard)(name)

    def click(self, name, count=1):
        for _ in range(count):
            self.button(name, True)
            self.button(name, False)

    def _key(self, name, down):
        self.kbd.emit(EV_KEY, KEYS[name], 1 if down else 0)
        self.kbd.syn()
        (self._held_keys.add if down else self._held_keys.discard)(name)

    def keys(self, combo):
        combo = [k for k in combo if k in KEYS]
        for k in combo:
            self._key(k, True)
        for k in reversed(combo):
            self._key(k, False)

    def tap(self, name, count=1):
        for _ in range(count):
            self._key(name, True)
            self._key(name, False)

    def type_text(self, text):
        """Type text. US-layout characters are real key presses; anything else
        (emoji, other languages) is pasted through the clipboard."""
        other = []
        for ch in text:
            if ch in CHARS:
                if other:
                    self._paste("".join(other))
                    other = []
                key, shift = CHARS[ch]
                if shift:
                    self._key("shift", True)
                self.tap(key)
                if shift:
                    self._key("shift", False)
            else:
                other.append(ch)
        if other:
            self._paste("".join(other))

    def _paste(self, text):
        if os.environ.get("WAYLAND_DISPLAY"):
            cmd = ["wl-copy", "--", text]
        else:
            cmd = ["xclip", "-selection", "clipboard"]
        try:
            subprocess.run(cmd, input=None if cmd[0] == "wl-copy" else text.encode(),
                           timeout=2, check=True)
        except (OSError, subprocess.SubprocessError):
            print("  ! Can't type non-English characters: install wl-clipboard (Wayland) or xclip (X11)")
            return
        time.sleep(0.05)
        self.keys(["ctrl", "v"])

    def zoom(self, notches):
        """Ctrl + mouse wheel. +notches = zoom in."""
        self._key("ctrl", True)
        self.mouse.emit(EV_REL, REL_WHEEL_HI_RES, notches * 120)
        self.mouse.emit(EV_REL, REL_WHEEL, notches)
        self.mouse.syn()
        self._key("ctrl", False)

    def release_all(self):
        for b in list(self._held_buttons):
            self.button(b, False)
        for k in list(self._held_keys):
            self._key(k, False)

    def close(self):
        self.release_all()
        self.mouse.close()
        self.kbd.close()
