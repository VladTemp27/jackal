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
Claude configuration directory under `~/.jackal/claude/<name>/`, but only the
launch model is isolated there. `settings.json` is a real, gateway-owned file
— rewritten before every launch as the normal profile's
`~/.claude/settings.json` with `model` set to that gateway's — and every other
entry is a symbolic link back to `~/.claude` (plus `.claude.json`, linked to
`~/.claude.json`). Native Claude Code state other than the model — agents,
skills, plugins, personal MCP servers, hooks, permissions, global
`CLAUDE.md`, history, login state — is therefore shared live with normal
`claude` and with every other gateway, not copied or isolated. A pre-existing
`~/.jackal.env` from an older version is migrated automatically, once, into a
gateway named `default`.

```
~/.jackal/
  work.env
  personal.env
  current
  claude/
    work/
      settings.json      # real file, gateway-owned, holds this gateway's model
      .claude.json      -> ~/.claude.json
      .credentials.json -> ~/.claude/.credentials.json
      agents/           -> ~/.claude/agents/
      plugins/          -> ~/.claude/plugins/
      ...               -> every other ~/.claude entry
```

`work.env` holds `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, any auto-mode
classifier aliases, and — only on a
gateway saved before model isolation shipped and not yet launched since — a
legacy `ANTHROPIC_MODEL` line. `claude/work/settings.json` is seeded by
`--setup` with the chosen model and rewritten before every later launch; the
links in `claude/work/` are created by `jackal`, once each, and left alone
after that. `jackal` only ever reads `~/.claude/settings.json` — it never
writes, repairs, or deletes anything in the normal profile.

A gateway created by the earlier, fully isolated build has real files where
links now belong. The first launch after upgrading renames each of those aside
with a `.jackal-isolated.bak` suffix and links the shared entry in its place,
printing one line saying so. Nothing is deleted — the gateway's old per-entry
state stays in the `.bak` files and can be removed by hand once you're happy
with the shared profile.

Up to seven environment variables are set, for one process only:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_BASE_URL` | points Claude Code at your gateway |
| `ANTHROPIC_AUTH_TOKEN` | bearer token sent to it |
| `ANTHROPIC_DEFAULT_SONNET_MODEL` | optional — gateway model used for Claude Code Sonnet background requests, including auto-mode safety classification |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` | optional — the same selected gateway model, used when Claude Code falls back to its Opus background route |
| `JACKAL_CLASSIFIER_CHECKED` | jackal's own marker, not read by Claude Code — records that `--setup` asked the auto-mode question, so launch knows not to warn |
| `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY` | set to `1` unless the gateway file overrides it; makes `/model` list what the gateway serves |
| `CLAUDE_CONFIG_DIR` | set to the gateway's `claude/<name>/` directory, so Claude Code reads and writes that gateway's own `settings.json`, and the normal profile through the links standing in for everything else |

`jackal` also removes any `ANTHROPIC_MODEL` inherited from the parent shell or
a legacy gateway file, so neither can redirect a launch away from the
gateway's stored model. They're set immediately before `os.execv`, which
**replaces** the jackal process rather than spawning a child — so `claude`
inherits them directly, and no wrapper process lingers. They apply to that one
process and nothing else: no `export` in your shell rc, no leakage into other
tools, and your normal `claude` profile under `~/.claude` is never written or
repaired — only linked into the gateway directory and, for `settings.json`,
read.

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

Whatever you pick seeds `~/.jackal/claude/<name>/settings.json` — the one file
in the gateway's directory that `jackal` owns — as its launch default, not
`ANTHROPIC_MODEL`. From inside the session, a native `/model` choice followed
by Enter persists there too, the same way it would in `~/.claude/settings.json`
for normal `claude`; pressing `s` after `/model` selects for that session only
and is not written anywhere. Because the gateway's model is always read from
`settings.json` before that file is rewritten for the next launch, a choice
made with Enter survives every later rewrite. A gateway saved before this
shipped may still carry a legacy `ANTHROPIC_MODEL` line in its `.env` — the
next launch migrates it into `settings.json` once and removes the line, and a
later native choice always wins over it. Model discovery for the `/model`
picker itself is turned on at launch rather than written into each gateway
file, so it works on every saved gateway, including ones created before this
feature shipped. None of this touches your normal Claude Code profile: its
model is neither copied into a gateway's `settings.json` nor repaired from
one, and every other setting in it — permissions, hooks, plugins, and the
rest — is read fresh into the gateway file on every launch, never written
back.

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
    number or model id, blank for gateway-gpt-5.6-sol, or skip
    ›
```

Enter reuses the launch model you just picked. Typing `skip` leaves both
aliases unset and warns that auto mode may be unavailable on this gateway.
Whatever is chosen is written to both
`ANTHROPIC_DEFAULT_SONNET_MODEL` and `ANTHROPIC_DEFAULT_OPUS_MODEL`, so the
same gateway model backs Claude Code's Opus fallback route too.

Running `--setup`/`--reconfigure` against an existing gateway replaces its
saved aliases rather than preserving them: skipping the Auto-mode prompt on a
reconfigure drops a previously-set pair instead of carrying it over.

Gateway files saved before this prompt existed were never asked the question,
so they pin no aliases and Claude Code asks the gateway for its own canonical
`claude-sonnet-*`/`claude-opus-*` ids instead. On a gateway that doesn't serve
those, auto mode fails the safety check and denies the tool call. jackal prints
a one-line reminder at launch when it finds such a file:

```text
  ·  no auto-mode model configured — auto mode may be unavailable
     re-run `jackal --setup` for this gateway to fix
```

Re-running `--setup` for that gateway clears it. The notice is suppressed when
output is piped, and never appears for a gateway that was asked — including one
serving canonical Claude ids natively, or where you deliberately chose `skip`.

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
