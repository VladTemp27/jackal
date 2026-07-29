# jackal

Launch Claude Code against a custom Anthropic gateway, without disturbing your
normal `claude`.

`jackal` is a single-file Python script with no dependencies. On first run it prompts for a base
URL and auth token, stores them in `~/.jackal.env` at `0600`, and from then on
execs `claude` with those in the environment. Your regular `claude` keeps using
your regular account.

```
  ╭────────────────────────────────────────╮
  │  jackal  ·  Claude via custom gateway  │
  ╰────────────────────────────────────────╯

  writing to ~/.jackal.env

  ▸ Anthropic base URL
    › https://gw.example.com

  ▸ Auth token   input hidden
    › 

  ✓  saved ~/.jackal.env  (0600, 19 chars)
```

## Install

Requires Python 3.9+ and [Claude Code](https://claude.com/claude-code)
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

## Use

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
gateways with `jackal --setup` never changes the default on its own — switch
it explicitly with `jackal use <name>`. If you only ever save one gateway,
`jackal` just uses it; if you save more than one and never pick a default,
`jackal` refuses to guess and tells you to run `jackal use <name>`.

Everything except `--setup` / `--reconfigure`, `use`, `--list`, `--remove`, and
`--gateway` is passed straight through to `claude`, so any flag or subcommand it
accepts works.

## Uninstall

```sh
npm un -g jackal-cli    # remove the command
rm -rf ~/.jackal        # remove every stored gateway URL and token
```

`npm un` removes the binary but leaves `~/.jackal/` behind — it holds live
credentials, so delete it explicitly if you're done with the gateways. If you
installed from source, `npm unlink -g jackal-cli` instead.

## How it works

Gateways are stored one-per-file under `~/.jackal/<name>.env` (same `0600`
permissions as before), with `~/.jackal/current` naming the default. A
pre-existing `~/.jackal.env` from an older version of jackal is migrated
automatically, once, into a gateway named `default`.

Two environment variables, set for one process only:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_BASE_URL` | points Claude Code at your gateway |
| `ANTHROPIC_AUTH_TOKEN` | bearer token sent to it |

They're set immediately before `os.execv`, which **replaces** the jackal process
rather than spawning a child — so `claude` inherits them directly, and no
wrapper process lingers. They apply to that one process and nothing else: no
`export` in your shell rc, no leakage into other tools.

`CLAUDE_CONFIG_DIR` is deliberately **not** set, so `jackal` shares your normal
`~/.claude` — same hooks, skills, agents, MCP servers, permissions, and
`CLAUDE.md`. Isolating it would mean rebuilding all of that behind
`--settings` / `--agents` / `--mcp-config` flags for no real benefit, because the
gateway authenticates via the environment while the stored OAuth credential
lives in the keychain. The two never collide.

## Banner

`jackal` prints a one-line banner naming the active gateway before handing off:

```
  ◆ jackal · gateway gw.example.com
```

Claude Code renders inline — no alt-screen, no clear-screen — so the banner
survives above its welcome box rather than being wiped. It shows the **host
only**, never the token, and is skipped when stdout is not a tty so
`jackal -p "..." > file` stays clean.

This affects `jackal` alone. `claude` and `jackal` are independent executables,
and nothing in your shell rc or `~/.claude/settings.json` references jackal —
running `claude` never executes this script.

## Tests

```sh
python3 test.py
```

Stdlib `unittest` and `pty`, no dev dependencies. Every test runs against a
throwaway `$HOME` with a stub `claude`, so it never touches your real config or
reaches a gateway. The pty tests self-skip on Windows.

### Notes on design

- **`os.execv` on POSIX** replaces the process outright, so Claude inherits the
  terminal, signals, and exit status directly — no wrapper left babysitting it.
  Windows has no `execve` (`os.execv` there detaches and returns immediately,
  so the shell prompt comes back mid-session), so that branch uses
  `subprocess.run` and propagates the exit code.
- **The terminal check is an actual `open()`.** `os.path.exists('/dev/tty')`
  and `os.access()` both succeed with no controlling terminal; only opening it
  raises `ENXIO`. Headless spawns — cron, CI, agent runners — depend on this
  failing fast instead of blocking on input forever.
- **Two tty handles, not one `"r+"`.** Buffered random access requires
  `seek()`, which a terminal has no notion of, so `open('/dev/tty', 'r+')`
  raises `io.UnsupportedOperation` — an `OSError` subclass that is
  indistinguishable from "no terminal" if caught broadly.
- **`getpass.getpass`** hides the token and restores echo even on
  `KeyboardInterrupt`, and works on Windows where `stty -echo` does not.
- **`os.open(..., 0o600)`** sets the mode at creation, so the credential file
  is never briefly world-readable the way a later `chmod` would allow.
- **`--setup` does not delete the old config first**, so an aborted reconfigure
  leaves working credentials intact.

## Compatibility

Requires Python 3.9+ (present by default on macOS and most Linux distributions).

| Platform | Status |
|---|---|
| Linux | tested in CI, Python 3.9 and 3.13 |
| macOS | tested in CI, Python 3.9 and 3.13 |
| Windows | tested in CI — native cmd/PowerShell, no WSL needed |
| BSD | POSIX paths only, untested |

### Windows requires `python3` on PATH

npm's Windows shim invokes the shebang interpreter **by name** — literally
`python3`, not `python`. The Microsoft Store build of Python provides
`python3.exe`; **the python.org installer does not**, it ships `python.exe` and
the `py` launcher. If `jackal` reports *"python3 is not recognized"*, either
install Python from the Store, or make an alias next to your `python.exe`:

```powershell
Copy-Item (Get-Command python).Source (Join-Path (Split-Path (Get-Command python).Source) python3.exe)
```

### Other known limits

- The box-drawing characters need a UTF-8 locale — `LANG=C` renders mojibake,
  and `NO_COLOR=1` strips colour but not the box.
- On Windows, `0o600` maps onto the read-only attribute rather than real POSIX
  permissions, so the credential file is less protected there than on Unix.
- The interactive first-run prompt is exercised by CI on Linux and macOS only;
  the pty-driven tests skip on Windows, so that path is covered there by
  design review rather than by test.

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
