"""Handing off to claude with the gateway's environment in place."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .gateways import (
    CLASSIFIER_CHECKED,
    JACKAL_DIR,
    config_value,
    gateway_claude_dir,
    gateway_path,
    host,
    link_profile,
    load_config,
    read_gateway_model,
    remove_config_key,
    rewrite_gateway_settings,
    write_gateway_model,
)
from .models import fetch_models, stale_pins, usable_model
from .setup import select_model
from .terminal import colors, open_tty
from .updates import maybe_check_for_update

CLAUDE_HINT = "install it with: npm i -g @anthropic-ai/claude-code"

MODEL_CHECK_CACHE = JACKAL_DIR / "model-check.json"
MODEL_CHECK_DISABLED = "JACKAL_NO_MODEL_CHECK"
MODEL_CHECK_INTERVAL = 24 * 60 * 60
# Far below MODELS_TIMEOUT: setup can afford to wait on a catalogue because
# the user is answering questions anyway, but this runs in front of every
# launch, where seconds of nothing would be worse than the warning is worth.
MODEL_CHECK_TIMEOUT = 2


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


def maybe_warn_stale_models(name, model):
    """One line when the gateway stopped serving something this gateway pins.

    The other half of warn_if_classifier_unconfigured, which deliberately
    stays offline and so can only report what the file records. This reports
    whether what the file records still exists: a gateway configured while
    the claude family was enabled keeps ANTHROPIC_DEFAULT_SONNET_MODEL=
    claude-sonnet-5 long after that family is switched off, and auto mode
    then fails with a 403 naming a model the user never chose.

    Shaped like maybe_check_for_update deliberately — terminal-only, cached
    for a day per gateway, and total in its error handling — so a slow,
    hostile, or unreachable gateway can never delay or break a launch.
    Silence is the default: a catalogue that could not be fetched proves
    nothing about the pins, so it says nothing rather than crying wolf.
    """
    if os.environ.get(MODEL_CHECK_DISABLED):
        return
    if not sys.stdout.isatty():
        return  # same rule as the banner: never corrupt piped output
    url = os.environ.get("ANTHROPIC_BASE_URL")
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not url or not token:
        return
    try:
        try:
            cache = json.loads(MODEL_CHECK_CACHE.read_text())
        except (OSError, ValueError):
            cache = {}
        now = int(time.time())
        if now - cache.get(name, 0) <= MODEL_CHECK_INTERVAL:
            return
        models, err = fetch_models(url, token, timeout=MODEL_CHECK_TIMEOUT)
        if err or not models:
            return
        # Stamped before the warning prints, so a gateway left unfixed nags
        # once a day rather than on every single launch.
        cache[name] = now
        JACKAL_DIR.mkdir(mode=0o700, exist_ok=True)
        MODEL_CHECK_CACHE.write_text(json.dumps(cache))
        stale = stale_pins(
            models,
            [
                model,
                os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL"),
                os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL"),
            ],
        )
        if not stale:
            return
        # Deduplicated: the two aliases hold the same id in the common case,
        # and naming it twice reads like two separate problems.
        c = colors()
        print(
            f"  {c['D']}·  gateway no longer serves"
            f" {', '.join(sorted(set(stale)))}{c['Z']}\n"
            f"  {c['D']}   re-run `jackal --setup` for this gateway to fix{c['Z']}\n"
        )
    except Exception:  # noqa: BLE001 — advisory only; must never break a launch
        return


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
        for key in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
            if key not in os.environ:
                sys.exit(f"jackal: gateway config '{gateway_path(name)}' missing {key}")
        # The catalogue is only useful to setup, which asks it a second
        # question; recovering a missing launch model does not.
        model, _ = select_model(
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
    model = _ensure_gateway_model(name, path)
    # Link the shared profile and refresh settings.json before handoff, so an
    # entry added to ~/.claude since the last launch is visible in this one,
    # and the rewritten file carries this launch's resolved model.
    link_profile(name)
    rewrite_gateway_settings(name, model)
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
    warn_if_classifier_unconfigured()
    maybe_warn_stale_models(name, model)

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
