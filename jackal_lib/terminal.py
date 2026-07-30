"""Terminal capabilities: colour support, and the controlling tty."""

import os


def colors():
    """ANSI codes, or empty strings when colour is unwanted or unsupported."""
    if os.environ.get("NO_COLOR") or os.environ.get("TERM", "dumb") == "dumb":
        return {k: "" for k in ("B", "D", "C", "G", "R", "Z")}
    if os.name == "nt":
        _enable_vt()
    return {
        "B": "\033[1m",
        "D": "\033[2m",
        "C": "\033[36m",
        "G": "\033[32m",
        "R": "\033[31m",
        "Z": "\033[0m",
    }


def _enable_vt():
    """Turn on ANSI processing in the Windows console (Win10+); harmless if it fails."""
    try:
        import ctypes

        k = ctypes.windll.kernel32
        k.SetConsoleMode(k.GetStdHandle(-11), 7)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except (AttributeError, OSError):
        return  # pre-Win10 console: ANSI simply won't render, nothing to do


def _stdin_is_console():
    """True only when stdin is a genuine Windows console handle.

    isatty() is not sufficient: Windows classifies NUL as a character device,
    so redirecting from NUL — which is what subprocess DEVNULL and `< NUL` do —
    reports as a tty. CONIN$ then opens successfully and the read blocks
    forever. GetConsoleMode succeeds only for a real console.
    """
    try:
        import ctypes
        from ctypes import wintypes

        k = ctypes.windll.kernel32
        mode = wintypes.DWORD()
        return bool(k.GetConsoleMode(k.GetStdHandle(-10), ctypes.byref(mode)))
    except (AttributeError, OSError):
        return False


def open_tty():
    """The controlling terminal as (reader, writer), or (None, None) if absent.

    Opening is the only reliable test. os.path.exists('/dev/tty') and
    os.access() both succeed with no controlling terminal — only the open
    raises ENXIO. Headless spawns (cron, CI, agent runners) land here.
    """
    try:
        if os.name == "nt":
            if not _stdin_is_console():
                return None, None
            return open("CONIN$", "r"), open("CONOUT$", "w")
        # Two handles, not one "r+": buffered random access requires seek(),
        # which a terminal has no notion of, so "r+" raises
        # io.UnsupportedOperation — an OSError subclass that looks exactly
        # like "no terminal here" if you catch it broadly.
        return open("/dev/tty", "r"), open("/dev/tty", "w")
    except OSError:
        return None, None
