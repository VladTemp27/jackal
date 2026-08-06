"""The interactive --setup flow.

Ordering is load-bearing: everything that can fail runs before the gateway file
is truncated, so an aborted setup leaves working credentials untouched.
"""

import getpass
import sys
from pathlib import Path

from .gateways import (
    CLASSIFIER_CHECKED,
    gateway_path,
    read_current,
    valid_name,
    write_current,
    write_gateway_config,
    write_gateway_model,
)
from .models import (
    _display_model_id,
    choose_model,
    fetch_models,
    has_claude_classifier_models,
    native_claude_model,
    usable_model,
)
from .terminal import colors


def select_model(url, token, out, tty_in):
    """The chosen launch model and the catalogue it was chosen from.

    Returns (model, models); model is None when nothing usable was picked.
    The catalogue comes back with it so a caller can ask further questions of
    the same fetch — setup needs it to decide whether auto mode has to be
    configured — without a second round-trip to the gateway.
    """
    c = colors()

    def w(s):
        out.write(s)
        out.flush()

    models, err = fetch_models(url, token)
    if err:
        w(
            f"\n  {c['D']}·  no model list from gateway ({err})"
            f" — enter a model id manually{c['Z']}\n"
        )
    elif not models:
        w(
            f"\n  {c['D']}·  gateway listed no models"
            f" — enter a model id manually{c['Z']}\n"
        )
    model = choose_model(models, w, tty_in, c)
    if not model:
        return None, models
    if not usable_model(model):
        w(
            f"\n  {c['R']}·{c['Z']}  {c['D']}model id {model!r} can't be stored safely{c['Z']}\n"
        )
        return None, models
    return model, models


def run_setup(out, tty_in):
    """Prompt for a gateway name, URL, token, and launch model; write to disk.

    Returns the saved gateway's name.
    """
    c = colors()

    def w(s):
        out.write(s)
        out.flush()

    def die(msg):
        w(f"\n  {c['R']}✗{c['Z']}  {msg}\n\n")
        sys.exit(1)

    w(f"\n  {c['C']}╭────────────────────────────────────────╮{c['Z']}\n")
    w(
        f"  {c['C']}│{c['Z']}  {c['B']}jackal{c['Z']}  ·  Claude via custom gateway  {c['C']}│{c['Z']}\n"
    )
    w(f"  {c['C']}╰────────────────────────────────────────╯{c['Z']}\n\n")

    w(f"  {c['C']}▸{c['Z']} {c['B']}Gateway name{c['Z']}\n    {c['D']}›{c['Z']} ")
    name = (tty_in.readline() or "").strip()
    if not valid_name(name):
        die(
            "gateway name must be non-empty and only letters, digits, - or _ — nothing saved"
        )

    path = gateway_path(name)
    short = str(path).replace(str(Path.home()), "~", 1)
    w(f"\n  {c['D']}writing to{c['Z']} {short}\n\n")

    w(f"  {c['C']}▸{c['Z']} {c['B']}Anthropic base URL{c['Z']}\n    {c['D']}›{c['Z']} ")
    url = (tty_in.readline() or "").strip()
    if not url.startswith(("http://", "https://")):
        die("URL must start with http:// or https:// — nothing saved")

    w(
        f"\n  {c['C']}▸{c['Z']} {c['B']}Auth token{c['Z']}   {c['D']}input hidden{c['Z']}\n"
    )
    # getpass reads from the terminal directly and restores echo even on
    # KeyboardInterrupt; it is the portable equivalent of stty -echo.
    token = getpass.getpass(f"    {c['D']}›{c['Z']} ", stream=out).strip()
    if not token:
        die("token required — nothing saved")

    # Nothing below may die() before the writes: the URL and token are going
    # to disk once a model is selected.
    model, models = select_model(url, token, out, tty_in)
    if not model:
        die("model required — nothing saved")

    # Only asked when claude's own classifier routes are missing: with both
    # canonical families present, auto mode already works and pinning aliases
    # would override a working default for no reason.
    auto_model = None
    if not has_claude_classifier_models(models):
        auto_model = choose_model(
            models,
            w,
            tty_in,
            c,
            title="Auto-mode model",
            # A canonical id the gateway advertises, in preference to the
            # launch model. Reaching here means the catalogue is missing one
            # of the two canonical families, but it may well have the other,
            # and these aliases are the one place Claude Code shows a model
            # id without decoding it first. Falls back to the launch model
            # when the gateway advertises no canonical id at all, which is
            # the only thing there was to offer before.
            default=native_claude_model(models) or model,
            allow_skip=True,
        )
        if auto_model and not usable_model(auto_model):
            w(
                f"\n  {c['R']}·{c['Z']}  {c['D']}model id {auto_model!r}"
                f" can't be stored safely — auto mode may be unavailable{c['Z']}\n"
            )
            auto_model = None
        if not auto_model:
            w(
                f"\n  {c['D']}·  no auto-mode model configured"
                f" — auto mode may be unavailable on this gateway{c['Z']}\n"
            )

    body = f"ANTHROPIC_BASE_URL={url}\nANTHROPIC_AUTH_TOKEN={token}\n"
    if auto_model:
        # Decoded, not as advertised. These two are the only ids claude
        # prints without decoding them first, so a cloaked value reads in
        # /model as its raw encoding while every neighbouring row shows a
        # name. Nothing is given up by decoding: a gateway using this cloak
        # is CLIProxyAPI, which serves the upstream name too — it advertises
        # only claude-fable-5-dd-hsalf-4v-keespeed and still answers 200 for
        # deepseek-v4-flash. Uncloaked ids pass through untouched.
        alias = _display_model_id(auto_model)
        body += f"ANTHROPIC_DEFAULT_SONNET_MODEL={alias}\n"
        body += f"ANTHROPIC_DEFAULT_OPUS_MODEL={alias}\n"
    else:
        # Records that auto mode was considered, which absence of the aliases
        # above cannot: they are equally absent for a gateway serving canonical
        # claude ids, for a deliberate skip, and for a file saved before any of
        # this existed. Only the last deserves a warning, so launch needs this
        # to tell them apart rather than nagging all three.
        body += f"{CLASSIFIER_CHECKED}=1\n"

    # Writing settings first ensures a settings failure leaves the existing
    # credential file untouched. Both destinations are replaced atomically,
    # so neither can be truncated halfway through a write.
    write_gateway_model(name, model)
    write_gateway_config(path, body)

    if read_current() is None:
        write_current(name)

    w(
        f'\n\n  {c["G"]}✓{c["Z"]}  saved gateway "{name}"  {c["D"]}(0600, {len(token)} chars){c["Z"]}\n'
    )
    # No `if model` guard: selection is required now, so it is always set.
    w(f"  {c['D']}   launch model {_display_model_id(model)}{c['Z']}\n")
    w("\n")
    return name
