"""The interactive --setup flow.

Ordering is load-bearing: everything that can fail runs before the gateway file
is truncated, so an aborted setup leaves working credentials untouched.
"""

import getpass
import sys
from pathlib import Path

from .gateways import (
    gateway_path,
    read_current,
    valid_name,
    write_current,
    write_gateway_config,
    write_gateway_model,
)
from .models import _display_model_id, choose_model, fetch_models, usable_model
from .terminal import colors


def select_model(url, token, out, tty_in):
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
        return None
    if not usable_model(model):
        w(
            f"\n  {c['R']}·{c['Z']}  {c['D']}model id {model!r} can't be stored safely{c['Z']}\n"
        )
        return None
    return model


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
    model = select_model(url, token, out, tty_in)
    if not model:
        die("model required — nothing saved")

    body = f"ANTHROPIC_BASE_URL={url}\nANTHROPIC_AUTH_TOKEN={token}\n"
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
