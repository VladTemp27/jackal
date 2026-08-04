# Design notes

Why `jackal` is built the way it is. Each entry records a decision that has a
non-obvious failure mode behind it — most were found by a test, not by review.

## Process handoff

**`os.execv` replaces the process; `subprocess` would not.** On POSIX, `jackal`
calls `os.execv`, so `claude` inherits the terminal, signals, and exit status
directly and no wrapper is left babysitting it.

Windows has no `execve` — `os.execv` there detaches and returns immediately, so
the shell prompt comes back mid-session. That branch uses `subprocess.run` and
propagates the exit code.

This is also why `jackal` is Python rather than TypeScript. Node has no
`execve` binding: every `child_process` "exec" forks a child, which would mean
hand-forwarding `SIGINT`, `SIGTERM`, `SIGWINCH`, stdio, and exit codes to a
full-screen TUI. Miss `SIGWINCH` and Claude's layout stops reflowing on
terminal resize.

## Detecting a terminal

**Detecting a controlling terminal requires opening `/dev/tty`, not testing
it.** `os.path.exists('/dev/tty')` and `os.access('/dev/tty', os.R_OK)` both
return true in a process with no controlling terminal; only `open('/dev/tty')`
fails, with `ENXIO`. `jackal` opens it, so headless spawns — cron, CI, agent
runners — fail fast instead of blocking on input forever.

**`open('/dev/tty', 'r+')` raises `io.UnsupportedOperation`, so `jackal` opens
two handles.** Buffered random access requires `seek()`, which a terminal has
no notion of. `io.UnsupportedOperation` is an `OSError` subclass, so a broad
`except OSError` cannot distinguish it from "no terminal here" — hence separate
read and write handles rather than one `"r+"`.

**On Windows, `isatty()` is not enough.** Windows classifies `NUL` as a
character device, so redirecting from it — which is what `subprocess.DEVNULL`
and `< NUL` do — reports as a tty, `CONIN$` then opens successfully, and the
read blocks forever. `GetConsoleMode` succeeds only for a real console, so that
is the check.

**`getpass.getpass` hides the token and restores echo even on
`KeyboardInterrupt`**, and works on Windows, where `stty -echo` does not.

## Credential handling

**`os.open(path, ..., 0o600)` sets the mode at creation**, so the credential
file is never briefly world-readable the way it would be between `open()` and a
later `chmod`. `jackal` also calls `chmod` afterwards, which is what corrects
the mode when the file already existed with looser permissions.

**`--setup` does not delete the old config first**, so an aborted reconfigure
leaves working credentials intact. The model fetch and the picker both run
*before* the file is opened for writing, which is what keeps that true now that
setup can talk to the network.

**The version is read from `package.json`, not hardcoded.** npm installs
`package.json` beside the package and a git checkout has it at the repo root,
so `--version` cannot disagree with what npm published. Hardcoding it once put
two different builds under the same version number.

## Per-gateway Claude profiles

Claude Code persists `/model` defaults to the active user settings file. A
process-only `ANTHROPIC_MODEL` selects launch behavior but cannot stop that
write, and snapshot/restore would race concurrent Claude sessions. Jackal
therefore sets a stable `CLAUDE_CONFIG_DIR` per gateway. This moves the write
boundary before it happens and preserves the direct `execv` handoff.

**A fully isolated profile per gateway was the first version, and it was
wrong.** Pointing `CLAUDE_CONFIG_DIR` at a directory holding a complete copy
of the Claude user profile is the smallest change and the strongest isolation
guarantee, but it silently amputates the rest of the tool: verified against
Claude Code 2.1.220, `CLAUDE_CONFIG_DIR=<gateway> claude mcp list` reported no
MCP servers at all, because personal MCP servers, agents, skills, plugins,
hooks, permissions, and login state all live in that same directory next to
the model, and none of it existed under the gateway's empty copy. A gateway
should change the model, not amputate the tool.

**Only `settings.json` is gateway-owned; everything else is a link.**
`CLAUDE_CONFIG_DIR` still redirects the whole profile root — Claude Code
offers no field-level persistence routing — so isolating just the model still
means isolating the one file it lives in. Every other entry in the gateway's
directory is a symbolic link back to `~/.claude` (plus `.claude.json`, which
normal Claude keeps outside `~/.claude` but which Claude expects inside
`CLAUDE_CONFIG_DIR`), so a gateway sees the exact agents, skills, plugins, MCP
servers, hooks, permissions, and login state normal `claude` does — live, with
no copying and no drift.

**`settings.json` is rewritten, not linked, because it mixes owned and shared
keys.** It carries the one key that must stay gateway-owned (`model`)
alongside keys that must stay shared (`permissions`, `hooks`,
`enabledPlugins`, `statusLine`); linking it would reopen the original bug and
isolating it outright would silently drop the user's permission rules and
disable every plugin. Jackal instead rewrites it before every launch as the
normal profile's `settings.json` with `model` overridden by the gateway's own
— read first, so a value persisted by native `/model` survives the rewrite. A
non-model preference changed inside a Jackal session does not persist: normal
Claude owns it, and the file is rebuilt from the normal profile on the next
launch. A malformed normal `settings.json` exits naming that file rather than
falling back to a model-only gateway file, which would silently drop the
user's permissions.

**A gateway from the earlier, fully isolated build is migrated on its next
launch.** It has real files where links now belong. Jackal renames each one
aside as `<entry>.jackal-isolated.bak` and links the shared entry in its
place, printing one summary line, so the fix reaches gateways that already
existed, not just new ones — nothing is deleted, and an entry that's already a
link is left alone. Where symbolic links can't be created — notably Windows
without Developer Mode or administrator rights — jackal reports one line and
launches anyway; model isolation does not depend on links.

## Talking to the gateway

**The model fetch catches `Exception`, deliberately.** Its contract is to return
`(models, message)` rather than leak parser or transport exceptions out of the
network boundary. A gateway serving only `/v1/messages` has to remain a
supported setup, so every way the fetch can fail comes back as a value setup
can mention and move past — asking for a model id by hand instead of dying with
a traceback on `IncompleteRead`, `BadStatusLine`, or deeply nested JSON.

**The bearer token is set with `add_unredirected_header`.** `urllib`'s redirect
handler copies ordinary headers onto the redirected request, so a gateway
answering `302` would hand your token to whatever host `Location` names — and
`--setup` accepts `http://` URLs, where a redirect can be injected. Unredirected
headers are not copied.

**The response is bounded in three directions at once**: a 15 second deadline
across the whole walk, a 2 MB cap per response, and a page limit. A socket
timeout alone only bounds *inactivity*, so a gateway trickling one byte just
inside it would never trip.

**What reaches the file is validated.** A model id has to survive a round trip
through `KEY=value` — an id carrying a newline would otherwise append a second
line that `jackal` reads back as a real variable on every later launch, which
could redirect the base URL and the token with it. Ids that can't be stored
intact are refused, and control characters are stripped from anything the
gateway asks to have printed, so a response can't redraw the picker to make one
entry look like another.

## Distribution

**npm's Windows shim invokes the shebang interpreter by name.** A
`#!/usr/bin/env python3` shebang becomes a `.cmd` that calls `python3`
literally, which is why Windows users need `python3` specifically on `PATH` —
see [troubleshooting](troubleshooting.md#windows-python3-is-not-recognized).

**`sys.path[0]` is the resolved script directory**, so `jackal_lib` is
importable through the symlink npm creates in its `bin` directory. This is
verified by an actual `npm i -g` in CI rather than assumed — it would work from
a git checkout and fail for every npm user otherwise.

## Why not Homebrew

A tap needs a separate `homebrew-jackal` repo, a release tarball, and a sha256
bump every version — for one script that npm already distributes. If this gets
enough traction to justify it, a formula is about fifteen lines.
