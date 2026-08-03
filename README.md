# jackal — Claude Code against a custom Anthropic gateway

[![npm](https://img.shields.io/npm/v/jackal-cli)](https://www.npmjs.com/package/jackal-cli)
[![ci](https://github.com/VladTemp27/jackal/actions/workflows/ci.yml/badge.svg)](https://github.com/VladTemp27/jackal/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/npm/l/jackal-cli)](LICENSE)

`jackal` (npm: `jackal-cli`) runs [Claude Code](https://claude.com/claude-code)
against a custom Anthropic-compatible endpoint by setting `ANTHROPIC_BASE_URL`
and `ANTHROPIC_AUTH_TOKEN` for one process, then `exec`ing `claude`. Your normal
`claude` command is unaffected: nothing is exported to your shell rc, and
`~/.claude/settings.json` is never written. Run `jackal` for gateway sessions and
`claude` for subscription sessions — both work at the same time, in two
terminals, with no switching step.

Pure-stdlib Python, no dependencies, MIT. It can save more than one named
gateway: `jackal --setup` prompts for a name, a base URL, a token, and a model,
and writes them to `~/.jackal/<name>.env` at mode `0600`. Every run after that
launches against the default gateway; switch it with `jackal use <name>`, or
override for one run with `jackal --gateway <name>`.

## Install

Requires Python 3.9+ and [Claude Code](https://claude.com/claude-code)
(`npm i -g @anthropic-ai/claude-code`).

```sh
npx jackal-cli            # try it, nothing installed permanently
npm i -g jackal-cli       # install `jackal` on your PATH
```

The first run prompts for a gateway name, base URL, and token, then offers the
models the gateway reports:

```
  ╭────────────────────────────────────────╮
  │  jackal  ·  Claude via custom gateway  │
  ╰────────────────────────────────────────╯

  ▸ Gateway name
    › work

  writing to ~/.jackal/work.env

  ▸ Anthropic base URL
    › https://gw.example.com

  ▸ Auth token   input hidden
    › 

  ▸ Launch model   3 from gateway
     1  Claude Opus 4.6     claude-opus-4-6
     2  Claude Sonnet 4.6   claude-sonnet-4-6
     3  Claude Haiku 4.5    claude-haiku-4-5
    number or model id (required)
    › 1

  ✓  saved gateway "work"  (0600, 42 chars)
     launch model claude-opus-4-6
```

A launch model is required — setup lists what the gateway reports at
`GET /v1/models` if it answers, but falls back to typing an id by hand rather
than skipping the picker.

## Usage

`jackal` takes the same arguments as `claude`. Everything except `--setup` /
`--reconfigure`, `use`, `--list`, `--remove`, `--gateway`, and `--version` is
passed straight through, so any flag or subcommand `claude` accepts works.

```sh
jackal                        # launches against the default gateway
jackal -p "hello"             # all arguments forward to claude untouched
jackal --setup                # add or edit a gateway: name, URL, token, model
jackal use work               # switch the default gateway to "work"
jackal --gateway work -p "hi" # one-off launch against "work", default unchanged
jackal --list                 # show saved gateways, marking the default
jackal --remove work          # delete a saved gateway
jackal --version              # jackal's version, and claude's
```

To **edit** a gateway, run `jackal --setup` and enter its existing name — it
replaces rather than edits, so every field is re-asked. To change one field,
edit `~/.jackal/<name>.env` directly; it's a plain `KEY=value` file.

## Documentation

| | |
|---|---|
| [Configuration](https://github.com/VladTemp27/jackal/blob/main/docs/configuration.md) | Where gateways live, the model picker, editing a gateway, the banner, update checks |
| [Design notes](https://github.com/VladTemp27/jackal/blob/main/docs/design.md) | `os.execv`, terminal detection, credential handling, gateway hardening |
| [Troubleshooting](https://github.com/VladTemp27/jackal/blob/main/docs/troubleshooting.md) | Compatibility, the Windows `python3` shim, error messages, known limits |
| [Contributing](https://github.com/VladTemp27/jackal/blob/main/CONTRIBUTING.md) | Layout, tests, lint, CI, release process |

## Your normal `claude` login is untouched

`jackal` does not sign you out of Claude Code and does not modify your saved
login.

Anthropic's gateway documentation states that setting `ANTHROPIC_AUTH_TOKEN`
"turns off subscription login **for that session**". `jackal` sets it in the
environment of exactly one process — the one it hands to `claude` — so the
effect ends when that process exits. Nothing is written to your shell rc,
nothing is written to `~/.claude/settings.json`, and `jackal` neither reads nor
writes Claude Code's stored credential.

In practice: `jackal` in one terminal talks to your gateway while `claude` in
another terminal talks to your subscription account, concurrently. Requests made
under `jackal` are billed to whatever account backs the gateway, not to your
subscription.

Each saved gateway has its own Claude configuration directory under
`~/.jackal/claude/<gateway>/`, but only the model is isolated there. Its
`settings.json` is a real, gateway-owned file, rewritten before every launch
from your normal profile's `~/.claude/settings.json` with `model` set to that
gateway's. Every other entry — agents, skills, plugins, personal MCP servers,
hooks, permissions, global `CLAUDE.md`, history, login state, and
`.claude.json` — is a symbolic link back to the normal profile, so a gateway
sees the exact same objects `claude` does, shared live with no copying and no
drift. A non-model setting changed inside a Jackal session does not persist:
`settings.json` is rebuilt from the normal profile on the next launch, because
normal Claude owns those settings. Repository-local `.claude`, `.mcp.json`,
and `CLAUDE.md` files still apply because Jackal launches from the same
working directory.

Gateway authentication still comes entirely from the gateway's `.env` file —
`jackal` neither copies nor modifies your ordinary Claude credentials to build
that directory; it only reads `~/.claude/settings.json` (never writes it) and
links the rest, then sets `CLAUDE_CONFIG_DIR` so Claude Code reads and writes
the gateway's directory instead of `~/.claude` directly.

## What counts as a gateway

`jackal` works with whatever Claude Code itself works with — anything that
serves the Anthropic Messages API over HTTP and accepts a bearer token:

- a [LiteLLM](https://docs.litellm.ai/) proxy, on `http://localhost:4000` or
  wherever you run it
- a corporate or team gateway that fronts Anthropic
- a local router that re-exposes another provider on an Anthropic-shaped endpoint
- your own relay

## What `jackal` does not do

`jackal` moves environment variables into the process. It performs no API
translation and carries no traffic.

- **No format translation.** The endpoint must already speak the Anthropic
  Messages API. An OpenAI-only endpoint needs a translating proxy — LiteLLM or
  equivalent — in front of it; point `jackal` at that proxy, not at the OpenAI
  endpoint.
- **No model routing.** `jackal` does not route between providers, fall back,
  or rewrite requests — whatever is at `ANTHROPIC_BASE_URL` still decides. It
  records a launch default in the gateway's own `settings.json` and turns on
  the gateway's own model discovery for `/model`, but neither routes a
  request anywhere.
- **Not for Bedrock or Vertex.** Those are selected with
  `CLAUDE_CODE_USE_BEDROCK` and `CLAUDE_CODE_USE_VERTEX`, not with a base URL.
- **Not in the request path.** Requests go from `claude` to your gateway
  directly.

## Alternatives

| Approach | Scope of the change | Normal `claude` still on your subscription? |
|---|---|---|
| `export` in your shell rc | every process in every new shell | no |
| `env` block in `~/.claude/settings.json` | every `claude` invocation | no |
| shell alias or function | every shell that sourced it | only if you maintain two names |
| Claude apps gateway (`/login`) | the signed-in session, until you sign out | no, until you sign back in |
| `jackal` | one process | yes |

[Claude apps gateway](https://code.claude.com/docs/en/claude-apps-gateway) is
Anthropic's own gateway, built into the `claude` binary, with IdP sign-in and
OTLP metrics. It is the right choice for an organization deploying a gateway.
`jackal` solves a smaller problem: one developer, one endpoint that already
exists, no change to how `claude` behaves the rest of the time.

## FAQ

### Does this log me out of my Claude subscription?

No. See [Your normal `claude` login is
untouched](#your-normal-claude-login-is-untouched) — the token is set for one
process, and your stored login is never read or written.

### Does it edit `~/.claude/settings.json` or my shell rc?

No. The only files `jackal` writes are under `~/.jackal/` — one `.env` file per
saved gateway, a `current` file naming the default, a small
`update-check.json` cache, and each gateway's directory under
`~/.jackal/claude/<gateway>/`. `jackal` does read `~/.claude/settings.json`,
to build each gateway's own copy with its model set, and lists `~/.claude`'s
other entries to link them into that directory — but it never writes,
repairs, or deletes anything under `~/.claude` or `~/.claude.json` itself.

### Does `jackal` ever phone home?

Only for the update check, and only when stdout is a real terminal: once a day
at most, it asks `registry.npmjs.org` for the latest published `jackal-cli`
version. No gateway URL, token, or usage data is ever sent — just that one GET
request. Set `JACKAL_NO_UPDATE_CHECK=1` to turn it off completely.

### If the gateway adds models later, does `/model` show them?

Yes — `jackal` has nothing to serve you a stale list from. See
[Configuration](https://github.com/VladTemp27/jackal/blob/main/docs/configuration.md#if-the-gateway-adds-models-later-does-model-show-them).

### `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`?

`jackal` writes `ANTHROPIC_AUTH_TOKEN`, which Claude Code sends as a bearer
token — what Anthropic documents for a gateway you run, and what most gateways
expect. A gateway that wants an `x-api-key` header instead needs
`ANTHROPIC_API_KEY`, which `jackal` does not set.

### Does it work non-interactively — CI, cron, an agent runner?

Once a gateway is configured, yes: with a default gateway already saved under
`~/.jackal/` nothing prompts, and the banner is skipped when stdout is not a
tty. The *first* run needs a real terminal and exits rather than blocking. In
CI, set the two variables directly — `jackal` is a convenience for humans, not
a dependency.

### Can I use it with Amazon Bedrock or Google Vertex?

No — see [What `jackal` does not do](#what-jackal-does-not-do).

### The context-used percentage is stuck near 100% — is that jackal?

No. Claude Code computes that percentage itself from the `usage` numbers in
each `/v1/messages` response; `jackal` has already handed off to `claude`
before any of those requests happen, so it can't see or fix them. A gateway
that under- or over-reports `cache_creation_input_tokens` /
`cache_read_input_tokens` will make the bar wrong from the first message on.
See [Troubleshooting](https://github.com/VladTemp27/jackal/blob/main/docs/troubleshooting.md#context-percentage-stuck-near-100)
for the mechanics and how to confirm it's the gateway.

## Uninstall

```sh
npm un -g jackal-cli    # remove the command
rm -rf ~/.jackal        # remove every stored gateway URL and token
```

`npm un` removes the binary but leaves `~/.jackal/` behind — it holds live
credentials, so delete it explicitly if you're done with the gateways. If you
installed from source, `npm unlink -g jackal-cli` instead.

## Security

`~/.jackal/` holds live credentials in plaintext at `0600`. It lives outside the
repo and `.gitignore` blocks `*.env` as a second line of defence, but they are
still files on disk — treat them like SSH keys.

Pointing `ANTHROPIC_BASE_URL` at a gateway routes every prompt, file, and diff
through whoever operates it. Fine for your own or your employer's
infrastructure; worth a deliberate decision for anyone else's.
