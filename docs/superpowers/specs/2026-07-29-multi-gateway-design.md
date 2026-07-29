# Multi-gateway support for jackal

## Problem

`jackal` currently stores exactly one gateway (`ANTHROPIC_BASE_URL` +
`ANTHROPIC_AUTH_TOKEN`) in a single flat file, `~/.jackal.env`. Anyone who
needs to launch Claude Code against more than one gateway (e.g. a work
gateway and a personal one) has no way to do it without manually editing
that file before every switch.

This spec adds named, saved gateways and a way to switch between them.

## Storage & migration

Replace the single file with a directory:

- `~/.jackal/<name>.env` — one file per gateway. Same `KEY=VALUE` body
  (`ANTHROPIC_BASE_URL=...` / `ANTHROPIC_AUTH_TOKEN=...`) and same `0600`
  permissions as today's `~/.jackal.env`. The existing read/write logic is
  reused essentially unchanged, just parameterized by path instead of
  hardcoded to `CFG`.
- `~/.jackal/current` — single line naming the default gateway (e.g.
  `work`). Written whenever a default is established (see fallback rules
  below).

**Gateway name validation:** names must match `^[A-Za-z0-9_-]+$` and be
non-empty. Names become part of a file path (`~/.jackal/<name>.env`), so
this is a trust-boundary check — it rejects path traversal (`../evil`),
empty names, and anything that could collide with the `current` file or
escape the directory. A name that fails validation is rejected the same
way an invalid URL is rejected today: an error message, exit 1, nothing
written.

**Migration:** on any invocation, if the old `~/.jackal.env` exists and
`~/.jackal/` has no gateways yet, it is moved (not copied) to
`~/.jackal/default.env` and `current` is set to `default`. Silent,
one-time, no user action required. After migration, `~/.jackal.env` no
longer exists (so this check is naturally a no-op on every subsequent
run).

## CLI surface

| Command | Behavior |
|---|---|
| `jackal` | Launch `claude` using the default gateway. See fallback rules below for what happens with zero, one, or many gateways and no default set. |
| `jackal --setup` (alias `--reconfigure`) | Interactive prompt, in order: gateway name, base URL, token. Saves/overwrites `~/.jackal/<name>.env` at `0600`. Aborting (bad URL, empty token) leaves that gateway's existing file untouched, same guarantee as today. If no default is currently set, this gateway becomes the default automatically. |
| `jackal use <name>` | Sets `<name>` as the default gateway. Errors (naming the unknown gateway, suggesting `jackal --list`) if `<name>` has no saved file. |
| `jackal --gateway <name> [claude args...]` | One-off launch against `<name>` without changing the default. Only recognized as `args[0]`/`args[1]` — consistent with how `--setup`/`--reconfigure` are only recognized at `args[0]` today. `jackal --gateway work -p "hi"` works; `jackal -p "hi" --gateway work` does not (passed straight through to `claude` untouched). |
| `jackal --list` | Prints each saved gateway's name and host, marking which one is the default. |
| `jackal --remove <name>` | Deletes `~/.jackal/<name>.env`. If `<name>` was the default, the default is cleared (the `current` file is removed), not silently reassigned to another gateway. |

Everything else continues to pass straight through to `claude` untouched,
same as today.

The banner gains the gateway's name alongside its host, since multiple
gateways may otherwise be hard to tell apart at a glance:

```
◆ jackal · gateway work · work-gw.example.com
```

## Fallback rules for bare `jackal`

1. Zero gateways exist → run the interactive setup flow (today's
   first-run experience, now asking for a name first).
2. One or more gateways exist and a default is set → use it.
3. One or more gateways exist, no default is set, and there is **exactly
   one** gateway → use it (no ambiguity, no reason to force `use`). This
   is a dynamic check, not a persisted choice: it does not write
   `current`, so adding a second gateway later makes the ambiguity (and
   rule 4) reappear on the next run.
4. Two or more gateways exist and no default is set (e.g. after
   `--remove` cleared the default) → error, listing the saved gateway
   names and instructing the user to run `jackal use <name>`. Jackal
   never guesses which of several gateways to use.
5. `current` names a gateway with no matching file (e.g. it was deleted
   outside of `jackal --remove`) → treat it the same as "no default set"
   and fall through to rule 3 or 4.

## Error handling

- Invalid gateway name (fails validation, or empty) → same `die()`-style
  message pattern as an invalid URL today; nothing written.
- `use <name>` / `--gateway <name>` / `--remove <name>` naming a gateway
  that doesn't exist → clear error naming the gateway and pointing at
  `jackal --list`.
- `use` / `--gateway` / `--remove` given with no following name argument
  → usage error, no crash/traceback.

## Testing plan

Extend `test.py` using the existing throwaway-`$HOME` + stub-`claude`
pattern (no new test infrastructure):

- Setting up two named gateways and switching the default with `use`.
- `--gateway <name>` one-off override leaves the default unchanged.
- `--list` output format and default marker.
- `--remove` of a non-default gateway (default untouched) and of the
  default gateway (default cleared).
- Silent migration of an old flat `~/.jackal.env` into
  `~/.jackal/default.env` with `current` set to `default`.
- Rejection of an invalid gateway name, including a path-traversal
  attempt (`../evil`), with no file written outside `~/.jackal/`.
- Fallback rule 3 (single gateway, no default set, auto-used).
- Fallback rule 4 (two-plus gateways, no default set, errors instead of
  guessing).

## Docs plan

Update `README.md`:

- **Use** section: document `use`, `--gateway`, `--list`, `--remove`.
- **Uninstall** section: `rm -rf ~/.jackal/` replaces `rm ~/.jackal.env`.
- **How it works**: describe the `~/.jackal/<name>.env` + `current` layout
  and the one-time migration from the old flat file.

## Out of scope

- Editing a gateway's URL/token without re-entering both (`--setup <name>`
  already overwrites the whole file; a partial-edit mode isn't needed).
- Renaming a saved gateway (remove + re-add covers it).
- Any interactive picker/menu UI — the design deliberately avoids it in
  favor of explicit `use`/`--gateway` so scripted/non-interactive
  invocations stay deterministic.
