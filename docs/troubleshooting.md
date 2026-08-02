# Troubleshooting

- [Compatibility](#compatibility)
- [Windows: "python3 is not recognized"](#windows-python3-is-not-recognized)
- [Error messages](#error-messages)
- [Model and configuration](#model-and-configuration)
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

## Error messages

| Message | Cause |
|---|---|
| `jackal: need a terminal to configure a gateway` | No gateway saved yet, and no controlling terminal — cron, CI, or stdin redirected from `NUL`/`DEVNULL`. Run `jackal --setup` once from a real terminal. |
| `jackal: Claude Code not found (looked for '...')` | `claude` is not on `PATH` and not at `~/.local/bin/claude`. Install with `npm i -g @anthropic-ai/claude-code`. |
| `gateway name must be non-empty and only letters, digits, - or _ — nothing saved` | Invalid gateway name at the `--setup` prompt. |
| `URL must start with http:// or https:// — nothing saved` | Base URL entered without a scheme. `gw.example.com` is rejected; `https://gw.example.com` is accepted. |
| `token required — nothing saved` | Empty token at the prompt. Nothing is written; any previous config is left intact. |
| `jackal: no gateway named '<name>' — see jackal --list` | `use`, `--gateway`, or `--remove` named a gateway that isn't saved. |
| `` jackal: multiple gateways saved, no default set — run `jackal use <name>` `` | Bare `jackal` with 2+ saved gateways and no default — run `jackal use <name>` to pick one. |
| `jackal: invalid Claude settings '<path>': <reason>` | The gateway's isolated `settings.json` is not valid JSON or not a JSON object. Back up and remove that file (see below), then run the gateway interactively to select a model again. |
| `jackal: legacy model '<x>' can't be stored safely` | A pre-migration `ANTHROPIC_MODEL` value in the gateway's `.env` contains characters that can't be safely written to `settings.json`. Run `jackal --gateway <name>` interactively and pick a model from the list instead. |

## Model and configuration

### Gateway needs a model after upgrading

Run `jackal --gateway <name>` in a terminal once and choose or type a model.
Headless launches refuse to guess.

### Reset one gateway's Claude profile

Back up and remove only `~/.jackal/claude/<name>/`, then run that gateway
interactively to select a model again. Do not remove `~/.claude` or
`~/.claude.json`; those belong to ordinary Claude.

## Known limits

- The box-drawing characters need a UTF-8 locale — `LANG=C` renders mojibake,
  and `NO_COLOR=1` strips colour but not the box.
- On Windows, `0o600` maps onto the read-only attribute rather than real POSIX
  permissions, so the credential file is less protected there than on Unix.
- The interactive first-run prompt is exercised by CI on Linux and macOS only;
  the pty-driven tests skip on Windows, so that path is covered there by design
  review rather than by test.
