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

A Python script behind a small Node launcher, no third-party dependencies in
either language, MIT. Node is not an extra requirement — installing from npm
already means you have it. `jackal` can save more than one
named gateway: `jackal --setup` prompts for a name, a base URL, and a token,
and writes them to `~/.jackal/<name>.env` at mode `0600`. Every run after that
launches against the default gateway; switch it with `jackal use <name>`, or
override for one run with `jackal --gateway <name>`.

## Install

Requires Node 14+ (already installed if you can run `npm`), Python 3.9+, and
[Claude Code](https://claude.com/claude-code)
(`npm i -g @anthropic-ai/claude-code`).

**npm** — run without installing, or install globally:

```sh
npx jackal-cli            # try it, nothing installed permanently
npm i -g jackal-cli       # install `jackal` on your PATH
```

**From source** — if you want to hack on it:

```sh
git clone https://github.com/VladTemp27/jackal.git && cd jackal
npm link
```

`npm link` symlinks `jackal` onto your PATH, so edits to the repo take effect
immediately with no reinstall step.

The first run prompts for a gateway name, base URL, and token:

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

  ✓  saved gateway "work"  (0600, 42 chars)
```

## Usage: running Claude Code through the gateway

`jackal` takes the same arguments as `claude`. Everything except `--setup` /
`--reconfigure`, `use`, `--list`, `--remove`, and `--gateway` is passed straight
through to `claude`, so any flag or subcommand it accepts works.

```sh
jackal                       # launches against the default gateway
jackal -p "hello"            # all arguments forward to claude untouched
jackal --setup                # add a new gateway or edit an existing one (prompts for its name)
jackal use work               # switch the default gateway to "work"
jackal --gateway work -p "hi" # one-off launch against "work" without changing the default
jackal --list                 # show saved gateways, marking the default
jackal --remove work          # delete a saved gateway
```

The first gateway you set up automatically becomes the default. Adding more
gateways with `jackal --setup` never changes an *already-set* default on its
own — switch it explicitly with `jackal use <name>`. If you only ever save one gateway,
`jackal` just uses it; if you save more than one and never pick a default,
`jackal` refuses to guess and tells you to run `jackal use <name>`.

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

`CLAUDE_CONFIG_DIR` is deliberately **not** set, so `jackal` shares your normal
`~/.claude` — the same hooks, skills, agents, MCP servers, permissions, and
`CLAUDE.md`. Isolating it would mean rebuilding all of that behind `--settings`,
`--agents`, and `--mcp-config` to solve a collision that does not occur.

## What counts as a gateway

`jackal` sets `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` and nothing else,
so it works with whatever Claude Code itself works with — anything that serves
the Anthropic Messages API over HTTP and accepts a bearer token:

- a [LiteLLM](https://docs.litellm.ai/) proxy, on `http://localhost:4000` or
  wherever you run it
- a corporate or team gateway that fronts Anthropic
- a local router that re-exposes another provider on an Anthropic-shaped endpoint
- your own relay

## What `jackal` does not do

`jackal` moves two strings into the environment. It performs no API translation
and carries no traffic.

- **No format translation.** The endpoint must already speak the Anthropic
  Messages API. An OpenAI-only endpoint needs a translating proxy — LiteLLM or
  equivalent — in front of it; point `jackal` at that proxy, not at the OpenAI
  endpoint.
- **No model routing.** `jackal` does not select models, fall back between
  providers, or rewrite requests. Whatever is at `ANTHROPIC_BASE_URL` decides.
- **Not for Bedrock or Vertex.** Those are selected with
  `CLAUDE_CODE_USE_BEDROCK` and `CLAUDE_CODE_USE_VERTEX`, not with a base URL.
- **Not in the request path.** Requests go from `claude` to your gateway
  directly.

## How it works: `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN`

Gateways are stored one-per-file under `~/.jackal/<name>.env` (same `0600`
permissions as before), with `~/.jackal/current` naming the default. A
pre-existing `~/.jackal.env` from an older version of jackal is migrated
automatically, once, into a gateway named `default`.

Two environment variables, set for one process only:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_BASE_URL` | points Claude Code at your gateway |
| `ANTHROPIC_AUTH_TOKEN` | bearer token sent to it |

They're set immediately before `os.execv`, which **replaces** the Python
process rather than spawning a child, so `claude` inherits them directly
instead of through a Python process that stuck around. (The Node launcher one
level up is a separate wrapper that does linger for the run — see Design
notes.) They apply to that one process and nothing else: no `export` in your
shell rc, no leakage into other tools.

## FAQ

### Does this log me out of my Claude subscription?

No. See [Your normal `claude` login is
untouched](#your-normal-claude-login-is-untouched) — the token is set for one
process, and your stored login is never read or written.

### Does it edit `~/.claude/settings.json` or my shell rc?

No. The only files `jackal` writes are under `~/.jackal/` — one `.env` file
per saved gateway, plus a `current` file naming the default.

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

## Banner: which gateway is active

`jackal` prints a one-line banner naming the active gateway before handing off:

```
  ◆ jackal · gateway work · gw.example.com
```

Claude Code renders inline — no alt-screen, no clear-screen — so the banner
survives above its welcome box rather than being wiped. It shows the
gateway's **name and host**, never the token, and is skipped when stdout is
not a tty so `jackal -p "..." > file` stays clean.

## Uninstall

```sh
npm un -g jackal-cli    # remove the command
rm -rf ~/.jackal        # remove every stored gateway URL and token
```

`npm un` removes the binary but leaves `~/.jackal/` behind — it holds live
credentials, so delete it explicitly if you're done with the gateways. If you
installed from source, `npm unlink -g jackal-cli` instead.

## Tests

```sh
python3 test.py
```

Stdlib `unittest` and `pty`, no dev dependencies. Every test runs against a
throwaway `$HOME` with a stub `claude`, so it never touches your real config or
reaches a gateway. The pty tests self-skip on Windows.

## Design notes: `os.execv`, `/dev/tty`, and `0600`

- **The Node launcher (`bin/jackal.js`) spawns Python; it does not exec it.**
  Node has nothing like `execve` to hand the process off and disappear, so a
  node process sits in front of Python on every platform — the price of
  resolving the interpreter ourselves rather than trusting whatever name npm's
  generated shim happens to look up (see the Windows note under
  Compatibility). It runs with `stdio: 'inherit'`, so the terminal, the
  tty/console checks below, and signal delivery to the foreground process
  group are unaffected; it exits with Python's own status and re-raises a
  killing signal on itself rather than inventing an exit code.
- **`os.execv` replaces the Python process; `subprocess` would not.** On POSIX,
  `jackal` calls `os.execv`, so `claude` inherits the terminal, signals, and exit
  status directly from Python — the one extra process in the chain is the Node
  launcher above it, not this handoff. Windows has no `execve`
  — `os.execv` there detaches and returns immediately, so the shell prompt comes
  back mid-session. That branch uses `subprocess.run` and propagates the exit
  code.
- **Detecting a controlling terminal requires opening `/dev/tty`, not testing
  it.** `os.path.exists('/dev/tty')` and `os.access('/dev/tty', os.R_OK)` both
  return true in a process with no controlling terminal; only
  `open('/dev/tty')` fails, with `ENXIO`. `jackal` opens it, so headless spawns
  — cron, CI, agent runners — fail fast instead of blocking on input forever.
- **`open('/dev/tty', 'r+')` raises `io.UnsupportedOperation`, so `jackal` opens
  two handles.** Buffered random access requires `seek()`, which a terminal has
  no notion of. `io.UnsupportedOperation` is an `OSError` subclass, so a broad
  `except OSError` cannot distinguish it from "no terminal here" — hence
  separate read and write handles rather than one `"r+"`.
- **On Windows, `isatty()` is not enough.** Windows classifies `NUL` as a
  character device, so redirecting from it — which is what `subprocess.DEVNULL`
  and `< NUL` do — reports as a tty, `CONIN$` then opens successfully, and the
  read blocks forever. `GetConsoleMode` succeeds only for a real console, so
  that is the check.
- **`getpass.getpass` hides the token and restores echo even on
  `KeyboardInterrupt`**, and works on Windows, where `stty -echo` does not.
- **`os.open(path, ..., 0o600)` sets the mode at creation**, so the credential
  file is never briefly world-readable the way it would be between `open()` and
  a later `chmod`. `jackal` also calls `chmod` afterwards, which is what corrects
  the mode when the file already existed with looser permissions.
- **`--setup` does not delete the old config first**, so an aborted reconfigure
  leaves working credentials intact.

## Compatibility

Requires Python 3.9+ (present by default on macOS and most Linux
distributions) and Node 14+, which running `jackal` via npm already
guarantees.

| Platform | Status |
|---|---|
| Linux | tested in CI, Python 3.9, 3.13 and 3.14 |
| macOS | tested in CI, Python 3.9, 3.13 and 3.14 |
| Windows | tested in CI — native cmd/PowerShell, no WSL needed |
| BSD | POSIX paths only, untested |

### Windows: Python is resolved automatically

`bin/jackal.js`, the npm-installed entry point, resolves the interpreter
itself instead of leaving Windows' shim to look up a hardcoded name. It tries
`python3`, then `python`, then `py -3`, running each candidate against a small
probe script and accepting one only if it echoes back a sentinel proving a
real Python 3.9+ actually ran it — a name that merely launches without error
doesn't count. Because of that, `python3` no longer has to be the name on
PATH: a stock python.org install, which ships `python.exe` and the `py`
launcher but never `python3.exe`, now works with no manual step.

If jackal still prints *"no usable Python 3.9+ found"*, the likely cause is
the Microsoft Store app-execution-alias stub: it claims a `python3` or
`python` name on PATH without being a real interpreter, prints something like
*"Python was not found"*, and exits `9009` without running anything. jackal
detects that signature and says so directly, pointing at Settings > Apps >
Advanced app settings > App execution aliases — turn the alias off there, or
install Python from the Store so the name resolves to something real.

### Errors

| Message | Cause |
|---|---|
| `jackal: need a terminal to configure a gateway` | No gateway saved yet, and no controlling terminal — cron, CI, or stdin redirected from `NUL`/`DEVNULL`. Run `jackal --setup` once from a real terminal. |
| `jackal: Claude Code not found (looked for '...')` | `claude` is not on `PATH` and not at `~/.local/bin/claude`. Install with `npm i -g @anthropic-ai/claude-code`. |
| `gateway name must be non-empty and only letters, digits, - or _ — nothing saved` | Invalid gateway name at the `--setup` prompt. |
| `URL must start with http:// or https:// — nothing saved` | Base URL entered without a scheme. `gw.example.com` is rejected; `https://gw.example.com` is accepted. |
| `token required — nothing saved` | Empty token at the prompt. Nothing is written; any previous config is left intact. |
| `jackal: no gateway named '<name>' — see jackal --list` | `use`, `--gateway`, or `--remove` named a gateway that isn't saved. |
| `` jackal: multiple gateways saved, no default set — run `jackal use <name>` `` | Bare `jackal` with 2+ saved gateways and no default — run `jackal use <name>` to pick one. |

### Other known limits

- The box-drawing characters need a UTF-8 locale — `LANG=C` renders mojibake,
  and `NO_COLOR=1` strips colour but not the box.
- On Windows, `0o600` maps onto the read-only attribute rather than real POSIX
  permissions, so the credential file is less protected there than on Unix.
- The interactive first-run prompt is exercised by CI on Linux and macOS only;
  the pty-driven tests skip on Windows, so that path is covered there by
  design review rather than by test.
- On Windows, a candidate that resolves to a `.bat` or `.cmd` shim rather than
  a real `.exe` — pyenv-win installs Python that way — cannot be launched
  without running it through a shell, which the launcher deliberately does not
  do because it forwards your arguments. Such a candidate fails the probe and
  is skipped, so those setups fall through to `py -3`. If you hit this with no
  `py` launcher installed, say so on the issue tracker.

## Why not Homebrew

A tap needs a separate `homebrew-jackal` repo, a release tarball, and a sha256
bump every version — for one script that npm already distributes. If this gets
enough traction to justify it, a formula is about fifteen lines.

## Security

`~/.jackal/` holds live credentials in plaintext at `0600`. It lives outside
the repo and `.gitignore` blocks `*.env` as a second line of defence, but they are
still files on disk — treat them like SSH keys.

Pointing `ANTHROPIC_BASE_URL` at a gateway routes every prompt, file, and diff
through whoever operates it. Fine for your own or your employer's infrastructure;
worth a deliberate decision for anyone else's.
