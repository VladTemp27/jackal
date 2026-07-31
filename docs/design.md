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

## Talking to the gateway

**The model fetch catches `Exception`, deliberately.** Its contract is that it
never raises, because a gateway serving only `/v1/messages` has to remain a
supported setup. An enumerated tuple of exception types kept missing cases that
each crashed `--setup` with a traceback: `IncompleteRead` and `BadStatusLine`
are `HTTPException`, not `OSError`, and `RecursionError` from deeply nested JSON
is a `RuntimeError`. Every branch returns the same `(models, message)` either
way, so narrowing bought nothing and cost live failure modes.

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
