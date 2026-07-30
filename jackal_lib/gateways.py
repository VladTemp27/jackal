"""Where gateways live on disk: naming, listing, migration, and loading.

Every path here is derived from JACKAL_DIR, which is what keeps jackal out of
~/.claude entirely.
"""

import os
import re
import sys
from pathlib import Path

from .terminal import colors

JACKAL_DIR = Path.home() / ".jackal"
CURRENT_FILE = JACKAL_DIR / "current"
OLD_CFG = Path.home() / ".jackal.env"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def valid_name(name):
    """True if name is safe to use as a filename component."""
    return bool(name) and bool(NAME_RE.match(name))


def gateway_path(name):
    return JACKAL_DIR / f"{name}.env"


def resolve_gateway(name):
    """A validated, existing gateway's path, or exit with a clear error."""
    if not valid_name(name):
        sys.exit(
            f"jackal: invalid gateway name '{name}' — letters, digits, - and _ only"
        )
    path = gateway_path(name)
    if not path.is_file():
        sys.exit(f"jackal: no gateway named '{name}' — see jackal --list")
    return path


def list_gateways():
    """Sorted names of all saved gateways."""
    if not JACKAL_DIR.is_dir():
        return []
    return sorted(p.stem for p in JACKAL_DIR.glob("*.env") if p.is_file())


def read_current():
    """The default gateway's name, or None if unset or it no longer exists."""
    if not CURRENT_FILE.is_file():
        return None
    name = CURRENT_FILE.read_text().strip()
    return name if valid_name(name) and gateway_path(name).is_file() else None


def write_current(name):
    JACKAL_DIR.mkdir(mode=0o700, exist_ok=True)
    CURRENT_FILE.write_text(name + "\n")


def migrate_old_config():
    """Move a pre-multi-gateway ~/.jackal.env into ~/.jackal/default.env."""
    if not OLD_CFG.is_file() or list_gateways():
        return
    JACKAL_DIR.mkdir(mode=0o700, exist_ok=True)
    OLD_CFG.rename(gateway_path("default"))
    os.chmod(gateway_path("default"), 0o600)
    write_current("default")


def load_config(path):
    """Read KEY=VALUE lines from path into the environment."""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip()


def host(url):
    """The bare host from a base URL, e.g. https://gw.test/v1 -> gw.test."""
    return url.split("://")[-1].split("/")[0]


def print_list():
    names = list_gateways()
    if not names:
        print("  no gateways saved — run: jackal --setup")
        return
    current = read_current()
    c = colors()
    for name in names:
        url = ""
        for line in gateway_path(name).read_text().splitlines():
            if line.startswith("ANTHROPIC_BASE_URL="):
                url = line.partition("=")[2].strip()
        mark = f"  {c['G']}(default){c['Z']}" if name == current else ""
        print(f"  {c['C']}▸{c['Z']} {name}{mark}   {c['D']}{host(url)}{c['Z']}")
