"""Handing off to claude with the gateway's environment in place."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .gateways import gateway_path, host, load_config
from .terminal import colors
from .updates import maybe_check_for_update

CLAUDE_HINT = "install it with: npm i -g @anthropic-ai/claude-code"
# Not a claude variable — jackal's own marker, written by setup to record that
# auto mode was considered for this gateway. Named JACKAL_ so it cannot collide
# with anything claude reads out of the inherited environment.
CLASSIFIER_CHECKED = "JACKAL_CLASSIFIER_CHECKED"


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


def warn_if_classifier_unconfigured():
    """One line when a gateway predates the auto-mode question.

    Such a file pins no classifier aliases, so claude asks the gateway for its
    own canonical claude-sonnet-/claude-opus- ids. A gateway that does not
    serve those fails the safety classification and auto mode denies the tool
    call — with an error naming a model the user never chose and no mention of
    jackal. Cheap to say so here; diagnosing it from that message is not.

    Deliberately not a fetch: launch stays offline, so this can only report
    what the file records, never re-check the catalogue.
    """
    if not sys.stdout.isatty():
        return  # same rule as the banner: never corrupt piped output
    if os.environ.get(CLASSIFIER_CHECKED):
        return
    if os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL") and os.environ.get(
        "ANTHROPIC_DEFAULT_OPUS_MODEL"
    ):
        return  # hand-written aliases are a deliberate answer to the question
    c = colors()
    print(
        f"  {c['D']}·  no auto-mode model configured — auto mode may be"
        f" unavailable{c['Z']}\n"
        f"  {c['D']}   re-run `jackal --setup` for this gateway to fix{c['Z']}\n"
    )


def launch(name, args):
    """Load a gateway's config, show the banner, and hand off to claude."""
    load_config(gateway_path(name))
    # Populates the in-session /model picker from the gateway's own
    # /v1/models. Set here rather than written per gateway file so gateways
    # saved before this existed get it too, with no migration. setdefault
    # after load_config means a line in the file still wins, which is the
    # whole opt-out — no extra flag needed.
    os.environ.setdefault("CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY", "1")
    maybe_check_for_update(version())
    banner(name)
    warn_if_classifier_unconfigured()

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
