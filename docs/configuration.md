# Configuration

How `jackal` stores gateways, picks a launch model, and what it prints.

- [Where gateways live](#where-gateways-live)
- [Choosing a model at setup](#choosing-a-model-at-setup)
- [Editing an existing gateway](#editing-an-existing-gateway)
- [The banner](#the-banner)
- [Update checks](#update-checks)

## Where gateways live

Gateways are stored one-per-file under `~/.jackal/<name>.env` at mode `0600`,
with `~/.jackal/current` naming the default. Each gateway also has its own
Claude user profile under `~/.jackal/claude/<name>/`, where its launch model
and any native Claude Code state — `/model` choices, agents, skills, plugins,
permissions, MCP configuration, history, login state — live in isolation from
`~/.claude` and from every other gateway. A pre-existing `~/.jackal.env` from
an older version is migrated automatically, once, into a gateway named
`default`.

```
~/.jackal/
  work.env
  personal.env
  current
  claude/
    work/
      settings.json
      .claude.json
      .credentials.json
      ...
```

`work.env` holds `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, and — only on a
gateway saved before this profile isolation shipped and not yet launched since
— a legacy `ANTHROPIC_MODEL` line. `claude/work/` is whatever Claude Code
itself writes there, seeded by `--setup` with a `settings.json` holding the
chosen model.

Up to four environment variables are set, for one process only:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_BASE_URL` | points Claude Code at your gateway |
| `ANTHROPIC_AUTH_TOKEN` | bearer token sent to it |
| `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY` | set to `1` unless the gateway file overrides it; makes `/model` list what the gateway serves |
| `CLAUDE_CONFIG_DIR` | set to the gateway's `claude/<name>/` directory, so Claude Code reads and writes only that gateway's isolated profile |

`jackal` also removes any `ANTHROPIC_MODEL` inherited from the parent shell or
a legacy gateway file, so neither can redirect a launch away from the isolated
profile's stored model. They're set immediately before `os.execv`, which
**replaces** the jackal process rather than spawning a child — so `claude`
inherits them directly, and no wrapper process lingers. They apply to that one
process and nothing else: no `export` in your shell rc, no leakage into other
tools, and your normal `claude` profile under `~/.claude` is neither shared
nor repaired.

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
    number or model id (required)
    ›
```

Answer with the list number, or type a model id directly — useful for an id the
gateway didn't list, or a catalogue too long to scroll. A model is required:
nothing is saved — not the URL, not the token — until one is chosen, because
an unpinned gateway can't launch headlessly (see
[below](#if-the-gateway-adds-models-later-does-model-show-them)).

A gateway that doesn't serve `/v1/models` — 404, unauthorized, timeout, or
simply unreachable — is a normal, supported setup, not an error. `--setup`
prints one warning line, skips the list, and still asks for a model id
directly. The fetch carries a 5 second timeout, so a wedged gateway can't hang
setup.

Whatever you pick seeds `~/.jackal/claude/<name>/settings.json` — the
gateway's own isolated Claude profile — as its launch default, not
`ANTHROPIC_MODEL`. From inside the session, a native `/model` choice followed
by Enter persists there too, the same way it would in `~/.claude/settings.json`
for normal `claude`; pressing `s` after `/model` selects for that session only
and is not written anywhere. A gateway saved before this shipped may still
carry a legacy `ANTHROPIC_MODEL` line in its `.env` — the next launch migrates
it into `settings.json` once and removes the line, and a later native choice
always wins over it. Model discovery for the `/model` picker itself is turned
on at launch rather than written into each gateway file, so it works on every
saved gateway, including ones created before this feature shipped. None of
this touches your normal Claude Code profile: its model is neither copied into
a gateway's profile nor repaired from one.

### If the gateway adds models later, does `/model` show them?

Yes. `jackal` has nothing to serve you a stale list from. The catalogue fetched
during `--setup` is used once, to draw the picker, and is then discarded — it is
never written to disk. A gateway's isolated `settings.json` holds a single
model, not a list, and `jackal` makes no network request at launch at all.

The one thing that does persist is your pinned launch model. A model added to
the gateway later will appear in `/model` but will not become your launch
default on its own, and if the gateway ever *removes* the model you pinned,
launches fail until you pick a new one — run `jackal --gateway <name>`
interactively and choose again from the picker or `/model`.

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
