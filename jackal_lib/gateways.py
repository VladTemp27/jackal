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

# The normal Claude user profile — read-only to jackal, never written,
# repaired, or copied. Derived from Path.home() (HOME/USERPROFILE), the same
# as JACKAL_DIR above, and never from CLAUDE_CONFIG_DIR: jackal overwrites
# that for its own launches, so it cannot be the source of truth for what
# "normal Claude" looks like.
NORMAL_CLAUDE_DIR = Path.home() / ".claude"
NORMAL_SETTINGS_PATH = NORMAL_CLAUDE_DIR / "settings.json"
NORMAL_CLAUDE_JSON_PATH = Path.home() / ".claude.json"

# Suffix a pre-existing real entry is renamed to before a gateway's own
# per-entry copy (written by the earlier, fully-isolated build) is replaced
# by a link. Rename, never delete: the old state stays recoverable.
BACKUP_SUFFIX = ".jackal-isolated.bak"


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

    A permission error here would otherwise surface as a raw traceback at
    launch; sys.exit naming the exact path that failed matches every other
    "cannot write isolated configuration" error in this module.
    """
    if path.is_dir():
        return
    _mkdir_secure(path.parent)
    try:
        path.mkdir(mode=0o700, exist_ok=True)
    except OSError as e:
        sys.exit(f"jackal: could not create '{path}': {e}")


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


def _read_json_object(path):
    """Parse path as a JSON object, or exit naming it. {} if it's absent.

    Shared by the gateway's own settings.json and the normal profile's, so
    both a malformed isolated file and a malformed normal file are reported
    the same way: sys.exit naming the exact file, never repaired or replaced.

    path.is_file() alone can't tell "absent" from "a dangling symlink" —
    both return False. Treating a dangling symlink as absent would silently
    fall back to a model-only file, dropping the user's permissions rules,
    exactly what this function exists to prevent. lexists catches the
    symlink whether or not its target exists, so only a genuinely missing
    path returns {}; anything else that isn't a regular file is an error.
    """
    if not path.is_file():
        if os.path.lexists(path):
            sys.exit(f"jackal: invalid Claude settings '{path}': not a regular file")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"jackal: invalid Claude settings '{path}': {e}")
    if not isinstance(data, dict):
        sys.exit(f"jackal: invalid Claude settings '{path}': expected a JSON object")
    return data


def _read_gateway_settings(name):
    return _read_json_object(gateway_settings_path(name))


def read_gateway_model(name):
    model = _read_gateway_settings(name).get("model")
    return model if isinstance(model, str) else None


def write_gateway_model(name, model):
    data = _read_gateway_settings(name)
    data["model"] = model
    _atomic_write(gateway_settings_path(name), json.dumps(data, indent=2) + "\n")


def _create_symlink(target, link_path, is_dir):
    """os.symlink, but a same-target race from a concurrent launch is success.

    lexists-then-symlink is two steps, not one: two launches sharing a
    gateway can both pass the lexists check before either calls symlink, so
    the loser sees FileExistsError for an entry that is, in fact, now
    linked exactly as it wanted. Re-checking islink turns that into a no-op
    instead of a spurious "could not link" report. Any other failure —
    including a real pre-existing non-link entry, which callers must rule
    out before reaching here — still raises.
    """
    try:
        os.symlink(target, link_path, target_is_directory=is_dir)
    except FileExistsError:
        if not os.path.islink(link_path):
            raise


def _link(target, link_path, failures, migrated, *, is_dir=False):
    """Symlink link_path -> target, migrating a pre-existing real entry aside.

    lexists (not exists) so a link_path that is itself a dangling symlink —
    e.g. a previous run's .claude.json link, before the target was ever
    written — still counts as "already there" and is left alone: no rename,
    no re-link, nothing to do.

    A real file or directory already at link_path — a gateway created by the
    earlier fully-isolated build, before entries were links at all — is
    renamed to `<name>BACKUP_SUFFIX` and then replaced by the link, so the
    fix reaches gateways that already exist, not just new ones.

    The rename and the symlink are two separate operations, so this is made
    all-or-nothing by hand: if the symlink fails (no Developer Mode on
    Windows, a filesystem that refuses symlinks, a permissions problem),
    the rename is undone before returning, restoring the exact pre-upgrade
    state. Without this, a systemic symlink failure — the common case, since
    one Windows install either allows symlinks or refuses all of them —
    would rename every entry aside and link none of them: worse than before
    the upgrade, on every single launch. The entry is only ever counted in
    `migrated` once both steps have actually succeeded. If the rollback
    itself fails, that is reported immediately and loudly, because it is
    the one case where the entry really is stranded at the backup name and
    the user needs to know where to find it.

    If the backup name is already taken (a previous migration already ran,
    or something else collided with it), the entry is left exactly alone
    rather than clobbering the earlier backup — reported once, since the
    gateway is silently keeping a stale isolated copy otherwise.

    Once moved aside, a later launch finds a link at link_path and takes
    the lexists fast path above, so migration never runs twice for the same
    entry. Ordinary link failures (rename, or symlink with no pre-existing
    entry to protect) are collected, not raised: one bad entry must not
    abort the others.
    """
    if os.path.lexists(link_path):
        if os.path.islink(link_path):
            return
        backup = link_path.with_name(link_path.name + BACKUP_SUFFIX)
        if os.path.lexists(backup):
            print(
                f"jackal: '{link_path}' already has a backup at '{backup.name}' "
                "from an earlier migration — leaving it as the gateway's own "
                "copy rather than overwriting that backup",
                file=sys.stderr,
            )
            return
        try:
            os.rename(link_path, backup)
        except OSError as e:
            failures.append((link_path.name, e))
            return
        try:
            _create_symlink(target, link_path, is_dir)
        except OSError as e:
            try:
                os.rename(backup, link_path)
            except OSError as rollback_err:
                print(
                    f"jackal: could not restore '{link_path}' after a failed "
                    f"link — your original entry is safe at '{backup}': "
                    f"{rollback_err}",
                    file=sys.stderr,
                )
            failures.append((link_path.name, e))
            return
        migrated.append(link_path.name)
        return
    try:
        _create_symlink(target, link_path, is_dir)
    except OSError as e:
        failures.append((link_path.name, e))


def link_profile(name):
    """Link the normal Claude profile's entries into a gateway's directory.

    Every entry of ~/.claude except settings.json (the one file that must
    stay gateway-owned), plus ~/.claude.json, which normal Claude keeps
    outside ~/.claude but which Claude expects inside CLAUDE_CONFIG_DIR. A
    real pre-existing entry is migrated aside and replaced by a link (see
    _link); a link already there from a prior launch is left alone. Entries
    added to the normal profile later get linked the next time this runs.

    A link that cannot be created (notably Windows without Developer Mode or
    admin rights) is not fatal: model isolation does not depend on links, so
    launch continues with whatever links exist, after one summary warning.
    Being unable to even list ~/.claude is the same story — reported once,
    not fatal, since it only affects sharing, not the model.
    """
    gdir = gateway_claude_dir(name)
    _mkdir_secure(gdir)
    failures = []
    migrated = []
    if NORMAL_CLAUDE_DIR.is_dir():
        try:
            entries = os.listdir(NORMAL_CLAUDE_DIR)
        except OSError as e:
            print(
                f"jackal: could not read '{NORMAL_CLAUDE_DIR}': {e} — linking "
                "skipped this launch",
                file=sys.stderr,
            )
            entries = []
        for entry in entries:
            if entry == "settings.json":
                continue
            target = NORMAL_CLAUDE_DIR / entry
            _link(target, gdir / entry, failures, migrated, is_dir=target.is_dir())
    _link(NORMAL_CLAUDE_JSON_PATH, gdir / ".claude.json", failures, migrated)
    if migrated:
        plural = "y" if len(migrated) == 1 else "ies"
        print(
            f"jackal: moved {len(migrated)} pre-existing entr{plural} in {gdir} "
            f"aside as *{BACKUP_SUFFIX} before linking the shared profile — "
            "your old per-gateway state is preserved there",
            file=sys.stderr,
        )
    if failures:
        first_name, first_err = failures[0]
        plural = "y" if len(failures) == 1 else "ies"
        print(
            f"jackal: could not link {len(failures)} profile entr{plural} into "
            f"{gdir} (e.g. '{first_name}': {first_err}) — continuing without "
            "them; any pre-existing entry among them is untouched, still at "
            "its original name",
            file=sys.stderr,
        )


# Keys that must never reach a gateway's settings.json from the normal
# profile: ANTHROPIC_MODEL there would resurrect the exact variable
# launch.py deliberately pops, silently defeating the gateway's pinned
# model; ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN would redirect gateway
# traffic — carrying the gateway's own token — to a different host.
_JACKAL_OWNED_ENV_KEYS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
)


def _strip_owned_settings(merged):
    """Remove jackal-owned keys from a settings dict copied from the normal
    profile. Mutates and returns `merged`; the caller's dict is a fresh
    json.loads() result, so the normal profile's file itself is never
    touched. Returns what was dropped, for a one-line warning.
    """
    dropped = []
    env = merged.get("env")
    if isinstance(env, dict):
        for key in _JACKAL_OWNED_ENV_KEYS:
            if key in env:
                del env[key]
                dropped.append(f"env.{key}")
        if not env:
            del merged["env"]
    if "apiKeyHelper" in merged:
        del merged["apiKeyHelper"]
        dropped.append("apiKeyHelper")
    return dropped


def rewrite_gateway_settings(name, model):
    """gateway settings.json := normal profile's settings.json, model set.

    model must already be read from the gateway's own settings.json before
    this runs (see read_gateway_model / _ensure_gateway_model), so a model
    persisted by native /model survives this rewrite. Non-model keys —
    permissions, hooks, enabledPlugins, statusLine — come from the normal
    profile fresh on every call and are never written back to it: normal
    Claude owns them, jackal only reads them. A malformed normal
    settings.json exits naming that file rather than silently dropping the
    user's permissions rules by falling back to a model-only file.

    A few keys are stripped rather than copied — see _strip_owned_settings —
    because Claude Code applies them itself: an `env` block persistently,
    and `apiKeyHelper` for credentials. Gateway auth and the gateway's model
    are jackal's alone to set; copying either verbatim would let the normal
    profile silently override them.
    """
    merged = _read_json_object(NORMAL_SETTINGS_PATH)
    dropped = _strip_owned_settings(merged)
    merged["model"] = model
    if dropped:
        print(
            f"jackal: dropped {', '.join(dropped)} from the normal profile's "
            f"settings copied into gateway '{name}' — gateway auth and model "
            "are jackal's, not the normal profile's, to set",
            file=sys.stderr,
        )
    _atomic_write(gateway_settings_path(name), json.dumps(merged, indent=2) + "\n")


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
