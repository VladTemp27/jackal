"""Passive update-availability check, with an inline y/n auto-update.

Depends only on terminal and gateways — the caller (launch.py) passes in the
current version rather than this module reading it itself, so launch.py can
import maybe_check_for_update without this module ever importing launch.py
back. That keeps the package's dependency graph a DAG.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

from .gateways import JACKAL_DIR
from .terminal import colors, open_tty

UPDATE_CACHE = JACKAL_DIR / "update-check.json"
UPDATE_URL = os.environ.get(
    "JACKAL_UPDATE_URL", "https://registry.npmjs.org/jackal-cli/latest"
)
UPDATE_CHECK_INTERVAL = 24 * 60 * 60


def _version_tuple(v):
    """'1.2.10' -> (1, 2, 10), so comparison is numeric, not lexicographic."""
    return tuple(int(p) for p in v.split("."))


def _read_update_cache():
    """The update-check cache as a dict, or {} if missing or corrupt."""
    try:
        return json.loads(UPDATE_CACHE.read_text())
    except (OSError, ValueError):
        return {}


def _write_update_cache(data):
    JACKAL_DIR.mkdir(mode=0o700, exist_ok=True)
    UPDATE_CACHE.write_text(json.dumps(data))


def _fetch_latest_version():
    """The latest published version from the registry, or None on any failure.

    URLError/HTTPError both subclass OSError, which also covers DNS and
    timeout failures; ValueError/KeyError cover a malformed or unexpected
    body. This must never raise — an unreachable or hostile registry cannot
    be allowed to break a launch.
    """
    try:
        with urllib.request.urlopen(UPDATE_URL, timeout=2) as resp:
            return json.loads(resp.read())["version"]
    except (OSError, ValueError, KeyError):
        return None


def _prompt_update(tty_in, tty_out, cache, latest):
    """Offer to run the update; record a decline so this version stops asking."""
    c = colors()

    def w(s):
        tty_out.write(s)
        tty_out.flush()

    w(f"  update now? {c['D']}[y/N]{c['Z']}\n    {c['D']}›{c['Z']} ")
    answer = (tty_in.readline() or "").strip().lower()

    if answer != "y":
        cache["declined"] = latest
        _write_update_cache(cache)
        return

    npm = shutil.which("npm")
    if not npm:
        w(
            f"\n  {c['R']}✗{c['Z']}  npm not found — update manually: "
            "npm i -g jackal-cli@latest\n\n"
        )
        return

    w(f"\n  {c['D']}updating…{c['Z']}\n")
    result = subprocess.run(
        [npm, "i", "-g", "jackal-cli@latest"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        w(f"  {c['G']}✓{c['Z']}  updated to {latest} — next launch uses it\n\n")
    else:
        w(
            f"  {c['R']}✗{c['Z']}  update failed — try manually: "
            "npm i -g jackal-cli@latest\n\n"
        )


def maybe_check_for_update(current_version):
    """Print a one-line notice and offer to update when a newer jackal-cli exists.

    A complete no-op — no network call, no output, no prompt — unless stdout
    is a real terminal (the same check banner() uses, so `jackal -p "..." >
    file` stays silent and non-interactive even when a controlling terminal
    is still attached) and there's a controlling terminal to read/write the
    prompt on (the open_tty() run_setup already relies on), and
    JACKAL_NO_UPDATE_CHECK is unset. Piped, scripted, and CI use are
    unaffected either way. Best-effort only: any failure here is swallowed
    so a flaky cache or hostile registry can never block a launch.
    """
    if os.environ.get("JACKAL_NO_UPDATE_CHECK"):
        return
    if not sys.stdout.isatty():
        return
    tty_in, tty_out = open_tty()
    if tty_in is None:
        return
    try:
        cache = _read_update_cache()
        now = int(time.time())
        if now - cache.get("checked_at", 0) > UPDATE_CHECK_INTERVAL:
            latest = _fetch_latest_version()
            if latest:
                cache["latest"] = latest
            cache["checked_at"] = now
            _write_update_cache(cache)

        latest = cache.get("latest")
        if not latest or latest == cache.get("declined"):
            return
        newer = _version_tuple(latest) > _version_tuple(current_version)
        if not newer:
            return

        c = colors()
        tty_out.write(
            f"\n  {c['C']}↑{c['Z']} update available "
            f"{c['D']}({current_version} → {latest}){c['Z']}\n"
        )
        tty_out.flush()
        _prompt_update(tty_in, tty_out, cache, latest)
    except Exception:  # noqa: BLE001 — cosmetic check, must never break a launch
        return
    finally:
        tty_in.close()
        if tty_out is not tty_in:
            tty_out.close()
