# Troubleshooting

- [Compatibility](#compatibility)
- [Windows: "python3 is not recognized"](#windows-python3-is-not-recognized)
- ["claude-opus-5 is temporarily unavailable" in auto mode](#claude-opus-5-is-temporarily-unavailable-in-auto-mode)
- [Error messages](#error-messages)
- [Model and configuration](#model-and-configuration)
- [Context percentage stuck near 100%](#context-percentage-stuck-near-100)
- [Known limits](#known-limits)

## Compatibility

Requires Python 3.9+ (present by default on macOS and most Linux
distributions).

| Platform | Status |
|---|---|
| Linux | tested in CI, Python 3.9 and 3.13 |
| macOS | tested in CI, Python 3.9 and 3.13 |
| Windows | tested in CI — native cmd/PowerShell, no WSL needed |
| BSD | POSIX paths only, untested |

## Windows: "python3 is not recognized"

Python is installed, but not under the name npm looks for.

npm's Windows shim invokes the shebang interpreter **by name** — literally
`python3`, not `python`. The Microsoft Store build of Python provides
`python3.exe`; **the python.org installer does not**, it ships `python.exe` and
the `py` launcher. This affects any npm package with a `#!/usr/bin/env python3`
shebang, not only `jackal`.

Install Python from the Microsoft Store, or add a `python3.exe` next to your
existing `python.exe`:

```powershell
Copy-Item (Get-Command python).Source (Join-Path (Split-Path (Get-Command python).Source) python3.exe)
```

## "claude-opus-5 is temporarily unavailable" in auto mode

Auto mode denies a tool call with a message naming `claude-sonnet-5` or
`claude-opus-5` — models you never chose, on a gateway that may not serve them
at all. The session itself works fine; only tool calls needing safety
classification fail, and read-only operations still succeed.

Claude Code classifies unsafe-looking tool calls through a separate request to
its *own* Sonnet default, falling back to its Opus default. Those are
independent of your launch model, so a gateway can run the session happily
while rejecting both classifier ids. Classification then can't complete, and
auto mode fails closed — denying the action rather than allowing an
unclassified one.

Check whether the gateway file pins the aliases:

```console
$ grep DEFAULT ~/.jackal/<name>.env
```

No output means the gateway was saved before `--setup` asked the auto-mode
question. Re-run `jackal --setup` for it and answer the **Auto-mode model**
prompt with a model the gateway actually serves. jackal also prints a reminder
at launch when it detects this — see
[configuration](configuration.md#auto-mode-model).

If the aliases *are* set and auto mode still fails, the gateway is genuinely
failing to serve the model they name; verify it appears in `/model`.

## Error messages

| Message | Cause |
|---|---|
| `jackal: need a terminal to configure a gateway` | No gateway saved yet, and no controlling terminal — cron, CI, or stdin redirected from `NUL`/`DEVNULL`. Run `jackal --setup` once from a real terminal. |
| `jackal: Claude Code not found (looked for '...')` | `claude` is not on `PATH` and not at `~/.local/bin/claude`. Install with `npm i -g @anthropic-ai/claude-code`. |
| `gateway name must be non-empty and only letters, digits, - or _ — nothing saved` | Invalid gateway name at the `--setup` prompt. |
| `URL must start with http:// or https:// — nothing saved` | Base URL entered without a scheme. `gw.example.com` is rejected; `https://gw.example.com` is accepted. |
| `token required — nothing saved` | Empty token at the prompt. Nothing is written; any previous config is left intact. |
| `model required — nothing saved` | No usable model came out of the picker — a blank line, an out-of-range number, or an id that can't be stored safely. A gateway that can't list `/v1/models` still lets you type an id by hand; nothing is written until one is chosen, and any previous config is left intact. |
| `no auto-mode model configured — auto mode may be unavailable on this gateway` | The gateway lacked a complete canonical Sonnet/Opus pair and classifier selection was skipped. Reconfigure and select a gateway model for auto mode. |
| `no auto-mode model configured — auto mode may be unavailable` at launch | The gateway file was saved before the auto-mode prompt existed, so it was never asked. Re-run `jackal --setup` for that gateway; see [configuration](configuration.md#auto-mode-model). |
| `jackal: no gateway named '<name>' — see jackal --list` | `use`, `--gateway`, or `--remove` named a gateway that isn't saved. |
| `` jackal: multiple gateways saved, no default set — run `jackal use <name>` `` | Bare `jackal` with 2+ saved gateways and no default — run `jackal use <name>` to pick one. |
| `jackal: invalid Claude settings '<path>': <reason>` | The gateway's own `settings.json` is not valid JSON or not a JSON object. Back up and remove that file (see below), then run the gateway interactively to select a model again. |
| `jackal: invalid Claude settings '~/.claude/settings.json': <reason>` | Your **normal** Claude settings file — not a gateway's — is not valid JSON or not a JSON object. Every gateway's `settings.json` is rewritten from this file before each launch, so jackal exits naming it rather than launching without your `permissions` rules. Fix `~/.claude/settings.json` directly; jackal only ever reads it, never repairs it. |
| `jackal: legacy model '<x>' can't be stored safely` | A pre-migration `ANTHROPIC_MODEL` value in the gateway's `.env` contains characters that can't be safely written to `settings.json`. Remove the `ANTHROPIC_MODEL` line from `~/.jackal/<name>.env`, then run `jackal --gateway <name>` interactively and pick a model. |

## Model and configuration

### Gateway needs a model after upgrading

Run `jackal --gateway <name>` in a terminal once and choose or type a model.
Headless launches refuse to guess.

### "moved N pre-existing entries ... aside as *.jackal-isolated.bak"

One-time notice, printed once per gateway, not an error. This gateway was
created by the earlier, fully isolated build and had real files — its own
`.claude.json`, `plugins/`, and so on — where a link to your normal profile
now belongs. Jackal renamed each one aside with a `.jackal-isolated.bak` suffix
and linked the shared entry in its place, so the gateway starts seeing your
agents, skills, plugins, personal MCP servers, and login state. Nothing was
deleted: the gateway's old per-entry state is still there under the `.bak`
names, and you can remove those files by hand once you're happy running with
the shared profile.

### "could not link N profile entries ... continuing without them"

Not fatal — Claude still launches, with whatever links exist. One or more
entries under `~/.claude` couldn't be symlinked into the gateway's directory.
This is expected on Windows without Developer Mode or administrator rights,
or on a filesystem that refuses symlinks (FAT/exFAT, some network mounts,
SELinux); model isolation does not depend on links.

Nothing is lost by this. If the entry never existed in the gateway
directory, it's simply missing for this launch, same as before. If the
gateway had its own real copy of that entry (left over from the earlier
fully isolated build), jackal only renames it aside once the link that
would replace it has actually succeeded — if the link fails, the rename is
undone first, so the entry ends up back under its original name, not
stranded at a `.bak` suffix. Enable Developer Mode, or run as administrator,
then relaunch to pick up the rest.

### "dropped ... from the normal profile's settings copied into gateway"

Not an error. Your `~/.claude/settings.json` sets something that decides where
requests go or how they authenticate — an `env` entry for `ANTHROPIC_MODEL`,
`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`,
`CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, or an `apiKeyHelper`.
Those are the gateway's to set, so `jackal` leaves them out of the copy it
writes for the gateway. Otherwise a personal key would be sent to whoever runs
the gateway, or the session would quietly talk to a different host or backend
than the banner shows. Everything else in your settings is copied through, and
your own `~/.claude/settings.json` is never modified — the drop applies only to
the gateway's copy. Normal `claude` still honours all of it.

### Reset one gateway's Claude profile

Back up and remove only `~/.jackal/claude/<name>/`, then run that gateway
interactively to select a model again — `jackal` recreates the directory,
reseeds `settings.json`, and relinks the rest of your normal profile into it.
Do not remove `~/.claude` or `~/.claude.json`; those belong to ordinary
Claude, and `jackal` never writes to them.

## Context percentage stuck near 100%

Claude Code's context bar computes
`(input_tokens + cache_creation_input_tokens + cache_read_input_tokens) / 200000`
from the `usage` object in each `/v1/messages` response, and divides by a
fixed 200k — it never asks the gateway for a window size. `jackal` sets four
environment variables and then `execv`s into `claude` before any HTTP request
is made, so it has no way to see or correct those numbers; there is nothing to
patch here.

If the percentage is wrong from the first message on, the gateway is very
likely mis-reporting one of those three fields — commonly a proxy that
translates from a non-Anthropic upstream and drops or estimates the
cache-write/cache-read token counts along the way. That's a gateway bug, not a
`jackal` one: report it to whoever operates the gateway. [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI),
for example, has tracked exactly this class of issue
([#4475](https://github.com/router-for-me/CLIProxyAPI/issues/4475),
[#4293](https://github.com/router-for-me/CLIProxyAPI/issues/4293)) on the path
where it translates a Codex/OpenAI upstream into Anthropic-shaped responses.

## Known limits

- The box-drawing characters need a UTF-8 locale — `LANG=C` renders mojibake,
  and `NO_COLOR=1` strips colour but not the box.
- On Windows, `0o600` maps onto the read-only attribute rather than real POSIX
  permissions, so the credential file is less protected there than on Unix.
- The interactive first-run prompt is exercised by CI on Linux and macOS only;
  the pty-driven tests skip on Windows, so that path is covered there by design
  review rather than by test.
