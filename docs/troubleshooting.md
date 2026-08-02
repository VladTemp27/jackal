# Troubleshooting

- [Compatibility](#compatibility)
- [Windows: "python3 is not recognized"](#windows-python3-is-not-recognized)
- ["claude-opus-5 is temporarily unavailable" in auto mode](#claude-opus-5-is-temporarily-unavailable-in-auto-mode)
- [Error messages](#error-messages)
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
| `could not list gateway models (...) — nothing saved` | `/v1/models` was missing, unauthorized, unreachable, malformed, incomplete, or too large. Fix the endpoint or token and rerun setup; an existing gateway is unchanged. If the gateway has no catalogue endpoint at all, write `~/.jackal/<name>.env` by hand instead — see [configuration](configuration.md#where-gateways-live) for the format. |
| `gateway exposes no usable models through /v1/models — nothing saved` | The endpoint returned no model IDs Jackal can safely store. Configure the gateway to expose at least one valid model. |
| `no auto-mode model configured — auto mode may be unavailable on this gateway` | The gateway lacked a complete canonical Sonnet/Opus pair and classifier selection was skipped. Reconfigure and select a gateway model for auto mode. |
| `no auto-mode model configured — auto mode may be unavailable` at launch | The gateway file was saved before the auto-mode prompt existed, so it was never asked. Re-run `jackal --setup` for that gateway; see [configuration](configuration.md#auto-mode-model). |
| `jackal: no gateway named '<name>' — see jackal --list` | `use`, `--gateway`, or `--remove` named a gateway that isn't saved. |
| `` jackal: multiple gateways saved, no default set — run `jackal use <name>` `` | Bare `jackal` with 2+ saved gateways and no default — run `jackal use <name>` to pick one. |

## Known limits

- The box-drawing characters need a UTF-8 locale — `LANG=C` renders mojibake,
  and `NO_COLOR=1` strips colour but not the box.
- On Windows, `0o600` maps onto the read-only attribute rather than real POSIX
  permissions, so the credential file is less protected there than on Unix.
- The interactive first-run prompt is exercised by CI on Linux and macOS only;
  the pty-driven tests skip on Windows, so that path is covered there by design
  review rather than by test.
