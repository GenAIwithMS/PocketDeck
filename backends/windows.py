"""Windows input backend using the Win32 SendInput API (standard library ctypes)."""
import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
ULONG_PTR = ctypes.c_size_t

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

BUTTON_FLAGS = {  # name: (down, up)
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}

KEYS = {  # name: virtual-key code
    "esc": 0x1B, "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "ctrl": 0x11,
    "shift": 0x10, "alt": 0x12, "space": 0x20, "super": 0x5B, "capslock": 0x14,
    "up": 0x26, "pageup": 0x21, "left": 0x25, "right": 0x27, "down": 0x28,
    "pagedown": 0x22, "home": 0x24, "end": 0x23, "insert": 0x2D, "delete": 0x2E,
    "printscreen": 0x2C, "menu": 0x5D,
    "mute": 0xAD, "volumedown": 0xAE, "volumeup": 0xAF,
    "nextsong": 0xB0, "playpause": 0xB3, "previoussong": 0xB1,
    "minus": 0xBD, "equal": 0xBB, "leftbrace": 0xDB, "rightbrace": 0xDD,
    "semicolon": 0xBA, "apostrophe": 0xDE, "grave": 0xC0, "backslash": 0xDC,
    "comma": 0xBC, "dot": 0xBE, "slash": 0xBF,
}
KEYS.update({c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"})
KEYS.update({f"f{i}": 0x6F + i for i in range(1, 13)})
EXTENDED = {"super", "up", "down", "left", "right", "pageup", "pagedown", "home",
            "end", "insert", "delete", "menu"}
KEYEVENTF_UNICODE = 0x0004


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _mouse(flags, dx=0, dy=0, data=0):
    i = INPUT(type=INPUT_MOUSE)
    i.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
    return i


def _keyev(name, down):
    vk = KEYS[name]
    flags = (0 if down else KEYEVENTF_KEYUP) | (KEYEVENTF_EXTENDEDKEY if name in EXTENDED else 0)
    i = INPUT(type=INPUT_KEYBOARD)
    i.ki = KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), flags, 0, 0)
    return i


class Injector:
    name = "Windows SendInput"

    def __init__(self):
        self._rx = self._ry = 0.0
        self._sv = self._sh = 0.0
        self._held_buttons = set()
        self._held_keys = set()

    def move(self, dx, dy):
        self._rx += dx
        self._ry += dy
        ix, iy = int(self._rx), int(self._ry)
        if not ix and not iy:
            return
        self._rx -= ix
        self._ry -= iy
        _send(_mouse(MOUSEEVENTF_MOVE, ix, iy))

    def scroll(self, h, v):
        """h/v in wheel notches (fractions allowed). +v = up, +h = right."""
        self._sv += v * 120
        self._sh += h * 120
        iv, ih = int(self._sv), int(self._sh)
        self._sv -= iv
        self._sh -= ih
        ev = []
        if iv:
            ev.append(_mouse(MOUSEEVENTF_WHEEL, data=iv))
        if ih:
            ev.append(_mouse(MOUSEEVENTF_HWHEEL, data=ih))
        if ev:
            _send(*ev)

    def button(self, name, down):
        down_f, up_f = BUTTON_FLAGS[name]
        _send(_mouse(down_f if down else up_f))
        (self._held_buttons.add if down else self._held_buttons.discard)(name)

    def click(self, name, count=1):
        for _ in range(count):
            self.button(name, True)
            self.button(name, False)

    def _key(self, name, down):
        _send(_keyev(name, down))
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
        """Type any Unicode text (all languages and emoji)."""
        events = []
        for ch in text:
            if ch in "\n\t":
                name = "enter" if ch == "\n" else "tab"
                events += [_keyev(name, True), _keyev(name, False)]
                continue
            data = ch.encode("utf-16-le")
            for i in range(0, len(data), 2):
                unit = int.from_bytes(data[i:i + 2], "little")
                for up in (0, KEYEVENTF_KEYUP):
                    inp = INPUT(type=INPUT_KEYBOARD)
                    inp.ki = KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | up, 0, 0)
                    events.append(inp)
        for i in range(0, len(events), 64):
            _send(*events[i:i + 64])

    def zoom(self, notches):
        self._key("ctrl", True)
        _send(_mouse(MOUSEEVENTF_WHEEL, data=notches * 120))
        self._key("ctrl", False)

    def release_all(self):
        for b in list(self._held_buttons):
            self.button(b, False)
        for k in list(self._held_keys):
            self._key(k, False)

    def close(self):
        self.release_all()
