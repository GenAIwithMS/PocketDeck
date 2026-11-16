import sys


def make_injector():
    if sys.platform.startswith("linux"):
        from .linux import Injector
    elif sys.platform == "win32":
        from .windows import Injector
    else:
        raise SystemExit("PocketDeck supports Windows and Linux only.")
    return Injector()
