# jackal

Launch Claude Code against a custom Anthropic gateway, without disturbing your
normal `claude`.

`jackal` is a ~70-line POSIX shell wrapper. On first run it prompts for a base
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

Requires [Claude Code](https://claude.com/claude-code)
(`npm i -g @anthropic-ai/claude-code`).

**npm** — run without installing, or install globally:

```sh
npx jackal-cli            # try it, nothing installed permanently
npm i -g jackal-cli       # install `jackal` on your PATH
```

**From source** — the source repository is private; if you have access:

```sh
git clone <this-repo> && cd jackal
./install.sh
```

`install.sh` symlinks `jackal` into `~/.local/bin` (override with
`JACKAL_BIN=/somewhere ./install.sh`). A symlink rather than a copy, so editing
the repo takes effect immediately.

## Use

```sh
jackal                  # first run prompts for URL + token, then launches
jackal -p "hello"       # all arguments forward to claude untouched
jackal --setup          # change the URL/token later (--reconfigure also works)
```

Everything except `--setup` / `--reconfigure` is passed straight through to
`claude`, so any flag or subcommand it accepts works.

## Uninstall

```sh
./uninstall.sh          # removes the symlink, keeps your credentials
./uninstall.sh --purge  # also deletes ~/.jackal.env
```

## How it works

Two environment variables, set for one process only:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_BASE_URL` | points Claude Code at your gateway |
| `ANTHROPIC_AUTH_TOKEN` | bearer token sent to it |

They're set inside the wrapper immediately before `exec`, so they apply to that
`claude` process and nothing else. No `export` in your shell rc, no leakage into
other tools.

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

This affects `jackal` alone. `claude` and `jackal` are independent symlinks to
different files, and nothing in your shell rc or `~/.claude/settings.json`
references jackal — running `claude` never executes this script.

### Notes on design

- Prompts read from `/dev/tty`, not stdin, so `jackal -p "..." < file` still works.
- The terminal check is `( : </dev/tty )`, not `test -r /dev/tty` — the latter
  stats the device node and returns true even with no controlling terminal.
  Without a real open attempt, headless spawns (cron, CI, agent runners) would
  hang or emit a raw `Device not configured`.
- The token is read with `stty -echo` rather than `read -s`, which is a
  bash/zsh extension that `dash` rejects.
- The config file is written inside `( umask 077; ... )` so it is `0600` from
  creation — `chmod` afterwards leaves a window where it is world-readable.
- `--setup` does not delete the old config first, so an aborted reconfigure
  leaves a working setup intact.

## Compatibility

| Platform | Status |
|---|---|
| macOS | tested |
| Linux | verified under `dash` (Debian/Ubuntu `/bin/sh`) |
| BSD | POSIX-only constructs, untested |
| Windows — WSL2 / Git Bash | works (both provide `/dev/tty` and `stty`) |
| Windows — cmd / PowerShell | not supported |

Known limits: the box-drawing characters need a UTF-8 locale (`LANG=C` renders
mojibake; `NO_COLOR=1` strips colour but not the box), and `~/.local/bin` must
be on `PATH`.

## Why not Homebrew

A tap from a **private** repo needs `HOMEBREW_GITHUB_API_TOKEN`, a separate
`homebrew-jackal` repo, a release tarball, and a sha256 bump every version — for
one shell script. `install.sh` is less machinery. If this ever goes public, a
formula is about fifteen lines and worth adding then.

## Security

`~/.jackal.env` holds a live credential in plaintext at `0600`. It lives outside
the repo and `.gitignore` blocks `*.env` as a second line of defence, but it is
still a file on disk — treat it like an SSH key.

Pointing `ANTHROPIC_BASE_URL` at a gateway routes every prompt, file, and diff
through whoever operates it. Fine for your own or your employer's infrastructure;
worth a deliberate decision for anyone else's.
