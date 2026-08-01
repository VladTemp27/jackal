"""Handing off to claude with the gateway's environment in place."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .gateways import (
    config_value,
    gateway_claude_dir,
    gateway_path,
    host,
    load_config,
    read_gateway_model,
    remove_config_key,
    write_gateway_model,
)
from .models import usable_model
from .setup import select_model
from .terminal import colors, open_tty
from .updates import maybe_check_for_update

CLAUDE_HINT = "install it with: npm i -g @anthropic-ai/claude-code"


def find_claude():
    """The claude executable, preferring PATH, falling back to the standard
    install path for spawns that inherit a minimal PATH (cron, CI, agents)."""
    return shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def version():
    """jackal's version, read from the package.json shipped beside it.

    Single source of truth: npm installs package.json next to this package and
    a git checkout has it at the repo root, so a bump cannot leave the code and
    the published version disagreeing.
    """
    pkg = Path(__file__).resolve().parent.parent / "package.json"
    try:
        return json.loads(pkg.read_text())["version"]
    except (OSError, ValueError, KeyError):
        return "unknown"


def print_versions():
    """Print jackal's version, and claude's when it can be determined.

    --version is a real claude flag, so intercepting it would otherwise cost
    the user the answer they were previously getting.
    """
    print(f"jackal {version()}")
    claude = find_claude()
    if not os.access(claude, os.X_OK):
        return
    try:
        out = subprocess.run(
            [claude, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return  # claude present but unrunnable; jackal's version still printed
    if out:
        print(f"claude {out}")


def banner(name):
    """One line naming the active gateway. Never the token, never when piped."""
    if not sys.stdout.isatty():
        return
    c = colors()
    gw_host = host(os.environ.get("ANTHROPIC_BASE_URL", ""))
    if c["C"]:
        print(
            f"\n  {c['C']}◆{c['Z']} {c['B']}jackal{c['Z']} {c['D']}· gateway{c['Z']} "
            f"{c['C']}{name}{c['Z']} {c['D']}·{c['Z']} {c['C']}{gw_host}{c['Z']}\n"
        )
    else:
        print(f"\n  jackal · gateway {name} · {gw_host}\n")


def _prompt_missing_model(name):
    """Interactively pick a launch model for name, or exit trying.

    Headless (no controlling tty) fails fast rather than launching claude
    unpinned: silently deferring to Claude Code's own default would be a
    surprise, not a feature.
    """
    tty_in, tty_out = open_tty()
    if tty_in is None:
        sys.exit(
            f'jackal: gateway "{name}" needs a model — run jackal interactively once'
        )
    try:
        model = select_model(
            os.environ["ANTHROPIC_BASE_URL"],
            os.environ["ANTHROPIC_AUTH_TOKEN"],
            tty_out,
            tty_in,
        )
    finally:
        tty_in.close()
        if tty_out is not tty_in:
            tty_out.close()
    if not model:
        sys.exit(f'jackal: gateway "{name}" needs a model')
    write_gateway_model(name, model)
    return model


def _ensure_gateway_model(name, path):
    """The gateway's model, migrating a legacy pin or prompting if unset.

    An isolated settings.json model wins over a legacy ANTHROPIC_MODEL line —
    set once at --setup time on newer jackal, but still readable on gateways
    saved before Task 1. Writing the isolated model happens before the legacy
    line is removed: if cleanup then fails, the .env survives with a
    redundant line, but the next launch sees the isolated model as
    authoritative and simply retries cleanup, never resetting the model.
    """
    model = read_gateway_model(name)
    legacy = config_value(path, "ANTHROPIC_MODEL")
    if model is not None and not usable_model(model):
        model = None
    if model is None and legacy:
        if not usable_model(legacy):
            sys.exit(f"jackal: legacy model {legacy!r} can't be stored safely")
        write_gateway_model(name, legacy)
        model = legacy
    if legacy and model is not None:
        remove_config_key(path, "ANTHROPIC_MODEL")
    if model is None:
        model = _prompt_missing_model(name)
    return model


def launch(name, args):
    """Load a gateway's config, show the banner, and hand off to claude."""
    path = gateway_path(name)
    load_config(path)
    _ensure_gateway_model(name, path)
    # Set after load_config so neither a parent shell nor an editable gateway
    # file can redirect Claude to normal user state: the isolated profile and
    # a cleared ANTHROPIC_MODEL are enforced last, not merely defaulted.
    os.environ["CLAUDE_CONFIG_DIR"] = str(gateway_claude_dir(name))
    os.environ.pop("ANTHROPIC_MODEL", None)
    # Populates the in-session /model picker from the gateway's own
    # /v1/models. Set here rather than written per gateway file so gateways
    # saved before this existed get it too, with no migration. setdefault
    # after load_config means a line in the file still wins, which is the
    # whole opt-out — no extra flag needed.
    os.environ.setdefault("CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY", "1")
    maybe_check_for_update(version())
    banner(name)

    claude = find_claude()
    if not os.access(claude, os.X_OK):
        sys.exit(
            f"jackal: Claude Code not found (looked for '{claude}')\n        {CLAUDE_HINT}"
        )

    if os.name == "nt":
        # Windows has no execve. os.execv there spawns a detached process and
        # exits immediately, so the shell returns before Claude finishes.
        # Wait explicitly and propagate the exit code instead.
        sys.exit(subprocess.run([claude, *args], check=False).returncode)
    os.execv(claude, [claude, *args])  # replaces this process entirely
