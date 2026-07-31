"""The interactive --setup flow.

Ordering is load-bearing: everything that can fail runs before the gateway file
is truncated, so an aborted setup leaves working credentials untouched.
"""

import getpass
import os
import sys
from pathlib import Path

from .gateways import JACKAL_DIR, gateway_path, read_current, valid_name, write_current
from .models import (
    choose_model,
    fetch_models,
    has_claude_classifier_models,
    usable_model,
)
from .terminal import colors


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

    models, err = fetch_models(url, token)
    if err:
        die(f"could not list gateway models ({err}) — nothing saved")
    if not models:
        die("gateway exposes no usable models through /v1/models — nothing saved")

    model = choose_model(models, w, tty_in, c)
    if model and not usable_model(model):
        w(
            f"\n  {c['R']}·{c['Z']}  {c['D']}model id {model!r} can't be stored"
            f" safely — no model pinned{c['Z']}\n"
        )
        model = None

    auto_model = None
    if not has_claude_classifier_models(models):
        auto_model = choose_model(
            models,
            w,
            tty_in,
            c,
            title="Auto-mode model",
            default=model,
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
    if model:
        body += f"ANTHROPIC_MODEL={model}\n"
    if auto_model:
        body += f"ANTHROPIC_DEFAULT_SONNET_MODEL={auto_model}\n"
        body += f"ANTHROPIC_DEFAULT_OPUS_MODEL={auto_model}\n"

    JACKAL_DIR.mkdir(mode=0o700, exist_ok=True)
    # O_CREAT with mode 0600 means the file is never briefly world-readable.
    # chmod afterwards also covers overwriting a pre-existing file.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    # Explicit utf-8 both here and in load_config: the locale encoding would
    # raise on a non-ASCII value *after* O_TRUNC has already emptied the file.
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(path, 0o600)

    if read_current() is None:
        write_current(name)

    w(
        f'\n\n  {c["G"]}✓{c["Z"]}  saved gateway "{name}"  {c["D"]}(0600, {len(token)} chars){c["Z"]}\n'
    )
    if model:
        w(f"  {c['D']}   launch model {model}{c['Z']}\n")
    w("\n")
    return name
