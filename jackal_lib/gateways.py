"""Where gateways live on disk: naming, listing, migration, and loading.

Every path here is derived from JACKAL_DIR, which is what keeps jackal out of
~/.claude entirely.
"""

import json
import os
import re
import sys
import tempfile
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


def gateway_claude_dir(name):
    return JACKAL_DIR / "claude" / name


def gateway_settings_path(name):
    return gateway_claude_dir(name) / "settings.json"


def _mkdir_secure(path):
    """Create path and any missing Jackal-owned ancestors, each 0700.

    Path.mkdir(mode=..., parents=True) only applies `mode` to the leaf
    directory — intermediate directories it creates along the way get the
    umask-default mode instead (typically 0755). Recurse so every level
    created for a gateway write (~/.jackal, ~/.jackal/claude, the gateway's
    own profile dir) ends up 0700, not just the last one.
    """
    if path.is_dir():
        return
    _mkdir_secure(path.parent)
    path.mkdir(mode=0o700, exist_ok=True)


def _atomic_write(path, body):
    _mkdir_secure(path.parent)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        # mkstemp already creates the temp file owner-only; os.chmod below
        # (cross-platform) re-asserts 0600 on the final path after the
        # atomic replace. os.fchmod is POSIX-only and unavailable on
        # Windows Python 3.9-3.12, so it must not be called here.
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_gateway_settings(name):
    path = gateway_settings_path(name)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"jackal: invalid Claude settings '{path}': {e}")
    if not isinstance(data, dict):
        sys.exit(f"jackal: invalid Claude settings '{path}': expected a JSON object")
    return data


def read_gateway_model(name):
    model = _read_gateway_settings(name).get("model")
    return model if isinstance(model, str) else None


def write_gateway_model(name, model):
    data = _read_gateway_settings(name)
    data["model"] = model
    _atomic_write(gateway_settings_path(name), json.dumps(data, indent=2) + "\n")


def write_gateway_config(path, body):
    _atomic_write(path, body)


def config_value(path, key):
    """The value of key in path's KEY=VALUE lines, or None if absent."""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        found, _, value = stripped.partition("=")
        if found.strip() == key:
            return value.strip()
    return None


def remove_config_key(path, key):
    """Atomically drop key's line from path, preserving every other line."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept = []
    changed = False
    for line in lines:
        stripped = line.strip()
        found = stripped.partition("=")[0].strip() if "=" in stripped else ""
        if found == key:
            changed = True
            continue
        kept.append(line)
    if changed:
        _atomic_write(path, "".join(kept))


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
