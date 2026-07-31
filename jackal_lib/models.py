"""The gateway's /v1/models catalogue, and picking a launch default from it.

Everything crossing this boundary is untrusted: the response decides what gets
printed to a terminal and what may be written into a KEY=value file, so ids are
gated by usable_model and display text has its control characters dropped.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

# ponytail: no test asserts this exact value — a closed port covers the same
# OSError branch instantly, without 5s of wall clock. Give it a real test if
# the value itself ever becomes contentious.
MODELS_TIMEOUT = 5
MODELS_PAGES = 10
# A per-read timeout only bounds inactivity: a gateway trickling one byte just
# inside it never trips. This bounds the whole walk, so "never blocks setup" is
# a guarantee rather than a hope pinned on Ctrl-C.
MODELS_DEADLINE = 15
MODELS_MAX_ID = 200
# Generous for a catalogue (hundreds of models run to tens of KB) and small
# enough that a gateway answering with something enormous — a streamed error
# page, a wrong endpoint — can't be read into memory until setup dies. Bytes
# and wall clock are bounded separately; MODELS_DEADLINE covers the latter.
MODELS_MAX_BYTES = 2 * 1024 * 1024


def _printable(s):
    """s with control characters removed."""
    return "".join(ch for ch in s if ch.isprintable())


def usable_model(mid):
    """True if mid can be written to a KEY=value file and read back intact.

    A model id arrives from the gateway or from whatever the user typed, and
    lands in a file load_config later splits on the first '=' per line. An id
    holding a newline would therefore write a second line that comes back as a
    real variable — a gateway could smuggle in its own ANTHROPIC_BASE_URL and
    quietly redirect the token. Anything unprintable or containing '=' is
    refused rather than written.

    The length bound is the same idea one step further: an id is also printed
    to a terminal, and no real model id is anywhere near this long.
    """
    return (
        bool(mid)
        and len(mid) <= MODELS_MAX_ID
        and "=" not in mid
        and mid == _printable(mid)
    )


def fetch_models(url, token, timeout=MODELS_TIMEOUT):
    """The gateway's /v1/models catalogue, as (models, error).

    models is a list of {"id", "display_name"}; error is None on success and a
    one-line reason otherwise. Both can be set at once: a gateway that serves
    page one and then fails still yields a usable list plus a warning.

    Never raises. A gateway implementing only /v1/messages is a supported
    setup rather than a failure, so every way this can go wrong has to come
    back as a value the caller can mention and move past.
    """
    endpoint = url.rstrip("/") + "/v1/models"
    models, after = [], None
    deadline = time.monotonic() + MODELS_DEADLINE
    try:
        # Bounded rather than `while has_more`: a gateway that repeats one
        # last_id forever would satisfy every in-loop check indefinitely.
        for _ in range(MODELS_PAGES):
            query = "?limit=100"
            if after:
                # The response is not ours; quote it rather than splicing it
                # into a URL as-is.
                query += "&after_id=" + urllib.parse.quote(after, safe="")
            req = urllib.request.Request(
                endpoint + query,
                headers={
                    "anthropic-version": "2023-06-01",
                    "accept": "application/json",
                },
            )
            # Bearer, not x-api-key: ANTHROPIC_AUTH_TOKEN is a bearer
            # credential, and this is the header claude itself sends after
            # execv — so a fetch that works here genuinely predicts a working
            # session. Unredirected, because HTTPRedirectHandler copies plain
            # headers onto a redirected request: a 302 would otherwise hand
            # the token to whatever host Location names.
            req.add_unredirected_header("Authorization", f"Bearer {token}")
            left = deadline - time.monotonic()
            if left <= 0:
                return models, f"gave up after {MODELS_DEADLINE}s"
            # urlopen has no default timeout at all, so without this a wedged
            # gateway would hang --setup outright.
            with urllib.request.urlopen(req, timeout=min(timeout, left)) as resp:
                # Read a bounded slice rather than json.load(resp), which
                # would pull the whole body in however big it claims to be.
                raw = resp.read(MODELS_MAX_BYTES + 1)
            if len(raw) > MODELS_MAX_BYTES:
                return models, f"response larger than {MODELS_MAX_BYTES} bytes"
            page = json.loads(raw)
            for m in page["data"]:
                mid = m["id"]
                # Drop what could never be stored instead of advertising it:
                # otherwise the picker offers a row that is refused after the
                # user picks it, and an id carrying an ANSI escape would reach
                # the terminal on the way. Everything surviving this is
                # printable and bounded, which is what makes the row below
                # safe to render.
                if not usable_model(mid):
                    continue
                models.append(
                    {
                        "id": mid,
                        # Same reasoning for the sibling field, which has no
                        # equivalent gate: drop control characters so a name
                        # can't redraw the list, and truncate so one verbose
                        # name can't wreck the layout.
                        "display_name": _printable(m.get("display_name") or mid)[:60],
                    }
                )
            after = page.get("last_id")
            # No last_id ends the walk even if has_more claims otherwise.
            if not page.get("has_more") or not after:
                break
        else:
            # Ran out of pages with more still advertised. Say so rather than
            # returning a silently truncated catalogue.
            return models, f"list truncated at {MODELS_PAGES} pages"
    except urllib.error.HTTPError as e:
        # The expected case: 404 for a gateway without the endpoint, 401 for a
        # token the endpoint won't accept. Worth a crisper line than str().
        return models, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001 — see below; the breadth is the point
        # Deliberately total. The contract above is "never raises", and an
        # enumerated tuple kept missing cases that each crashed --setup with a
        # traceback: IncompleteRead and BadStatusLine are HTTPException rather
        # than OSError, and RecursionError from deeply nested JSON is a
        # RuntimeError. Every branch here returns the same shape anyway, so
        # narrowing buys nothing and costs a live failure mode.
        return models, f"{type(e).__name__}: {e}".splitlines()[0][:120]
    return models, None


def choose_model(models, w, tty_in, c):
    """The chosen model id, or None to leave Claude Code's default alone.

    Accepts a list number or a model id typed verbatim. The advertised list
    can be a subset of what a gateway will actually serve — undated aliases
    resolve on gateways that omit them from data[] — so typing an id that
    isn't listed has to keep working.
    """
    w(
        f"\n  {c['C']}▸{c['Z']} {c['B']}Launch model{c['Z']}   "
        f"{c['D']}{len(models)} from gateway{c['Z']}\n"
    )
    # Pad so the ids line up, capped so one verbose display_name can't push
    # every id off the right edge.
    pad = min(28, max(len(m["display_name"]) for m in models))
    for i, m in enumerate(models, 1):
        w(
            f"    {c['C']}{i:>2}{c['Z']}  {m['display_name']:<{pad}}"
            f"   {c['D']}{m['id']}{c['Z']}\n"
        )
    w(f"    {c['D']}number, model id, or blank to skip{c['Z']}\n")
    w(f"    {c['D']}›{c['Z']} ")
    answer = (tty_in.readline() or "").strip()
    if not answer:
        return None
    # isdecimal, not isdigit: isdigit accepts characters int() rejects, so '²'
    # — a dedicated key next to 1 on AZERTY — would pass the guard and then
    # raise ValueError out of int().
    if answer.isdecimal():
        n = int(answer)
        if 1 <= n <= len(models):
            return models[n - 1]["id"]
        # A bare number outside the list is a typo, not a model id. Treating it
        # as one would pin something like "12" and surface the mistake much
        # later as an opaque error from Claude Code.
        w(f"    {c['D']}no entry {n} — no model pinned{c['Z']}\n")
        return None
    return answer
