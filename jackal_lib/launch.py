"""Handing off to claude with the gateway's environment in place."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .gateways import gateway_path, host, load_config
from .terminal import colors

CLAUDE_HINT = "install it with: npm i -g @anthropic-ai/claude-code"


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


def launch(name, args):
    """Load a gateway's config, show the banner, and hand off to claude."""
    load_config(gateway_path(name))
    # Populates the in-session /model picker from the gateway's own
    # /v1/models. Set here rather than written per gateway file so gateways
    # saved before this existed get it too, with no migration. setdefault
    # after load_config means a line in the file still wins, which is the
    # whole opt-out — no extra flag needed.
    os.environ.setdefault("CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY", "1")
    banner(name)

    claude = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
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
