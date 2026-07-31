# Configuration

How `jackal` stores gateways, picks a launch model, and what it prints.

- [Where gateways live](#where-gateways-live)
- [Choosing a model at setup](#choosing-a-model-at-setup)
- [Editing an existing gateway](#editing-an-existing-gateway)
- [The banner](#the-banner)
- [Update checks](#update-checks)

## Where gateways live

Gateways are stored one-per-file under `~/.jackal/<name>.env` at mode `0600`,
with `~/.jackal/current` naming the default. A pre-existing `~/.jackal.env`
from an older version is migrated automatically, once, into a gateway named
`default`.

```
~/.jackal/
  work.env              ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, optional ANTHROPIC_MODEL
  personal.env
  current               the default gateway's name
  update-check.json     cache for the once-daily update check
```

Up to four environment variables are set, for one process only:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_BASE_URL` | points Claude Code at your gateway |
| `ANTHROPIC_AUTH_TOKEN` | bearer token sent to it |
| `ANTHROPIC_MODEL` | optional — the launch default chosen at `--setup`; absent if you skipped the picker |
| `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY` | set to `1` unless the gateway file overrides it; makes `/model` list what the gateway serves |

They're set immediately before `os.execv`, which **replaces** the jackal
process rather than spawning a child — so `claude` inherits them directly, and
no wrapper process lingers. They apply to that one process and nothing else: no
`export` in your shell rc, no leakage into other tools.

The first gateway you set up automatically becomes the default. Adding more
with `jackal --setup` never changes an *already-set* default on its own —
switch it explicitly with `jackal use <name>`. If you only ever save one
gateway, `jackal` just uses it; if you save more than one and never pick a
default, `jackal` refuses to guess and tells you to run `jackal use <name>`.

## Choosing a model at setup

Right after the token is validated, `--setup` fetches `GET /v1/models` from the
gateway and offers a numbered picker for the model Claude Code should launch
with:

```
  ▸ Launch model   3 from gateway
     1  Claude Opus 4.6     claude-opus-4-6
     2  Claude Sonnet 4.6   claude-sonnet-4-6
     3  Claude Haiku 4.5    claude-haiku-4-5
    number, model id, or blank to skip
    ›
```

Answer with the list number, or type a model id directly — useful for an id the
gateway didn't list, or a catalogue too long to scroll. Leave it blank to skip:
nothing is written, and Claude Code's own default stands.

A gateway that doesn't serve `/v1/models` — 404, unauthorized, timeout, or
simply unreachable — is a normal, supported setup, not an error. `--setup`
prints one warning line, skips the picker, and still saves the URL and token.
The fetch carries a 5 second timeout, so a wedged gateway can't hang setup.

Whatever you pick is written as `ANTHROPIC_MODEL` and only sets what the
session launches with. Switch it any time from inside the session with
`/model`, which asks the gateway for its catalogue directly — on every saved
gateway, including ones created before this feature shipped, since discovery is
turned on at launch rather than written into each gateway file.

### If the gateway adds models later, does `/model` show them?

Yes. `jackal` has nothing to serve you a stale list from. The catalogue fetched
during `--setup` is used once, to draw the picker, and is then discarded — it is
never written to disk. A gateway file holds a single model **id**, not a list,
and `jackal` makes no network request at launch at all.

The one thing that does persist is your pinned `ANTHROPIC_MODEL`. A model added
to the gateway later will appear in `/model` but will not become your launch
default on its own, and if the gateway ever *removes* the model you pinned,
launches fail until you change that line. Leave the pin blank at setup if you'd
rather track whatever the gateway defaults to.

## Editing an existing gateway

Run `jackal --setup` and enter the **existing** gateway's name. It is a
replace, not an edit: every field is re-asked, nothing is prefilled, and a
model you don't re-pick is not carried over.

Run `jackal --list` first to get the name exactly right — a typo creates a
*second* gateway rather than editing the one you meant.

To change a single field, edit the file directly. It's a plain `KEY=value`
file:

```sh
$EDITOR ~/.jackal/work.env
```

Leave the permissions at `0600`; it holds a live token.

## The banner

`jackal` prints one line naming the active gateway before handing off:

```
  ◆ jackal · gateway work · gw.example.com
```

Claude Code renders inline — no alt-screen, no clear-screen — so the banner
survives above its welcome box rather than being wiped. It shows the gateway's
**name and host**, never the token, and is skipped when stdout is not a tty so
`jackal -p "..." > file` stays clean.

## Update checks

On an interactive launch, `jackal` checks npm for a newer `jackal-cli` at most
once every 24 hours (cached in `~/.jackal/update-check.json`) and, if one
exists, shows:

```
  ↑ update available (0.2.0 → 0.3.0)
  update now? [y/N]
    ›
```

Answering `y` runs `npm i -g jackal-cli@latest` and reports success or failure;
anything else skips that specific version without asking again until a newer one
ships.

Like the banner, this is skipped entirely — no network call, no output —
whenever stdout isn't a tty, so piped, scripted, and CI use are unaffected. Set
`JACKAL_NO_UPDATE_CHECK=1` to disable it outright.

This exists because npm does **not** notify you about outdated global packages.
It notifies about new versions of npm itself, which is why people assume
otherwise; `npm outdated -g` reports package updates only when you run it.
