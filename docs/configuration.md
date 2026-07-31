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

Up to six environment variables are set, for one process only:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_BASE_URL` | points Claude Code at your gateway |
| `ANTHROPIC_AUTH_TOKEN` | bearer token sent to it |
| `ANTHROPIC_MODEL` | optional — the launch default chosen at `--setup`; absent if you skipped the picker |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | optional — gateway model used for Claude Code Sonnet background requests, including auto-mode safety classification |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | optional — the same selected gateway model, used when Claude Code falls back to its Opus background route |
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
gateway — authenticated with the same bearer token, and fully paginated to
walk a catalogue larger than one page — and offers a numbered picker for the
model Claude Code should launch with:

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

`/v1/models` is mandatory, not a convenience: a fetch error, a parse or
pagination failure, or a catalogue with no usable model ids aborts `--setup`
before the gateway file is touched, so an existing gateway is left exactly as
it was. The launch model choice above stays optional either way.

### Auto-mode model

Claude Code's auto mode routes safety classification through its own
background Sonnet and Opus requests. If the gateway's catalogue already has
both a canonical `claude-sonnet-*` and a canonical `claude-opus-*` id, those
routes work unmodified and `--setup` skips straight past this prompt. If
either family is missing, `--setup` asks for an Auto-mode model:

```
  ▸ Auto-mode model   3 from gateway
     1  GPT 5.6 Sol          gateway-gpt-5.6-sol
     2  Kimi K2.6            gateway-kimi-k2.6
     3  GLM 5.1              gateway-glm-5.1
    number, model id, blank for gateway-gpt-5.6-sol, or skip
    ›
```

Enter reuses whatever you picked as the launch model; if you left the launch
model blank there's nothing to reuse, so `--setup` skips the alias and warns
that auto mode may be unavailable. Typing `skip` explicitly leaves both
aliases unset. Whatever is chosen is written to both
`ANTHROPIC_DEFAULT_SONNET_MODEL` and `ANTHROPIC_DEFAULT_OPUS_MODEL`, so the
same gateway model backs Claude Code's Opus fallback route too.

Running `--setup`/`--reconfigure` against an existing gateway replaces its
saved aliases rather than preserving them: skipping the Auto-mode prompt on a
reconfigure drops a previously-set pair instead of carrying it over.

Whatever you pick as the launch model is written as `ANTHROPIC_MODEL` and only
sets what the session launches with. Switch it any time from inside the
session with `/model`, which asks the gateway for its catalogue directly — on
every saved gateway, including ones created before this feature shipped, since
discovery is turned on at launch rather than written into each gateway file.

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
