# Jackal Gateway Model Isolation Design

## Status

Approved and implemented.

Revised after implementation: the first version isolated the entire Claude user profile, which stripped gateways of personal MCP servers, agents, plugins, hooks, permissions, and login state. Isolation is now scoped to `settings.json`, the only file that carries the model.

## Problem

Jackal launches Claude Code against a custom gateway by setting gateway-specific environment variables and then handing the process to Claude. Jackal currently leaves `CLAUDE_CONFIG_DIR` unset, so Jackal sessions and ordinary `claude` sessions share Claude's user configuration.

Claude Code 2.1.220 gives the native `/model` picker two actions:

- **Enter** selects the model and saves it as the default for future sessions.
- **s** selects the model for the current session only.

The persistent action writes the `model` setting to the active Claude user configuration. Because Jackal currently uses the normal Claude configuration directory, pressing Enter in a Jackal session changes the default later read by ordinary `claude` sessions.

The existing `ANTHROPIC_MODEL` launch pin is not a complete solution. It changes the current Jackal process, but it does not prevent `/model` from writing the shared user setting. It also has higher launch precedence than a saved settings model, so keeping it permanently would cause a later `/model` default to be ignored on the next Jackal launch.

## Goals

1. A persistent model selected through native `/model` is scoped to the current Jackal gateway.
2. Native picker behavior remains unchanged: Enter persists, while **s** is session-only.
3. Different gateways can keep different defaults.
4. Ordinary `claude` user configuration and its current model are never modified or automatically repaired.
5. Existing gateways with `ANTHROPIC_MODEL` migrate without losing their selected model.
6. Existing gateways without a model require an explicit selection after upgrade.
7. Repository-local Claude configuration continues to apply inside Jackal sessions.
8. Everything in the Claude user profile except the model — agents, skills, plugins, personal MCP servers, hooks, permissions, global `CLAUDE.md`, history, and login state — stays shared with normal Claude and is never copied or synchronized.
9. POSIX retains the current `os.execv` handoff; Windows retains its wait-and-propagate behavior.
10. The implementation uses only the Python standard library and the existing module structure.

## Non-goals

1. Giving a gateway its own hooks, agents, skills, plugins, permissions, history, or login state. Those stay owned by the normal Claude profile and are shared into every gateway.
2. Writing non-model user preferences back from a gateway to the normal Claude profile. Jackal only ever reads them.
3. Repairing a normal Claude model that may already have been changed by an earlier Jackal session.
4. Replacing Claude's native `/model` picker with a Jackal-specific in-session picker.
5. Defining stronger conflict semantics than normal last-writer-wins behavior for two concurrent sessions using the same gateway.
6. Isolating repository-local `.claude`, `.mcp.json`, or `CLAUDE.md` configuration.

## Decision

Give each gateway its own Claude user configuration directory, but isolate only the one file that carries the model. Everything else in the directory is a symbolic link back to the normal Claude profile:

```text
~/.jackal/
  work.env
  personal.env
  current
  claude/
    work/
      settings.json        # real file, gateway-owned, holds this gateway's model
      .claude.json      -> ~/.claude.json
      .credentials.json -> ~/.claude/.credentials.json
      agents/           -> ~/.claude/agents/
      plugins/          -> ~/.claude/plugins/
      rules/            -> ~/.claude/rules/
      CLAUDE.md         -> ~/.claude/CLAUDE.md
      ...               -> every other ~/.claude entry
    personal/
      settings.json        # real file, gateway-owned
      ...                  # same links
```

For a gateway named `work`, Jackal launches Claude with:

```text
CLAUDE_CONFIG_DIR=~/.jackal/claude/work
```

`CLAUDE_CONFIG_DIR` redirects the whole user profile root, and Claude Code offers no field-level persistence routing, so isolating the model necessarily isolates the file it lives in. Only `settings.json` is therefore gateway-owned. Native `/model` writes there and cannot reach the normal Claude default.

Every other entry is a link, so agents, skills, plugins, personal MCP servers, hooks, login state, and history are the same objects normal Claude uses — live, with no copying, syncing, or drift. A gateway is a different model, not a different Claude.

Gateway authentication still comes from the gateway's `ANTHROPIC_AUTH_TOKEN`, which is independent of the shared Claude credential store. Repository-local Claude configuration remains in effect because the working directory is unchanged and project configuration is independent of the user profile root.

### Gateway `settings.json`

`settings.json` mixes the one key that must be isolated (`model`) with keys that must stay shared (`permissions`, `hooks`, `enabledPlugins`, `statusLine`). Linking the file would restore the original bug; isolating it outright would silently drop permission rules and disable every plugin.

Jackal therefore rewrites the gateway file before each launch:

```text
gateway settings.json = normal Claude settings.json, with "model" set to the gateway's model
```

The gateway's model is read from the gateway file first, so a model persisted by native `/model` survives the rewrite. Non-model preferences are read from the normal profile every launch and never written back: normal Claude owns them, and a non-model preference changed inside a Jackal session does not persist.

If the normal `settings.json` is malformed, Jackal exits and names the file. It must not silently fall back to a model-only file, because that would drop the user's `permissions` rules.

The copy is not entirely verbatim. Two kinds of keys are stripped before the gateway file is written, because Claude Code applies them itself rather than treating them as inert data:

- `env.ANTHROPIC_MODEL`, `env.ANTHROPIC_BASE_URL`, `env.ANTHROPIC_AUTH_TOKEN`, `env.ANTHROPIC_API_KEY`, `env.CLAUDE_CODE_USE_BEDROCK`, and `env.CLAUDE_CODE_USE_VERTEX` — Claude Code's `env` block is applied persistently across launches. Left in place, `ANTHROPIC_MODEL` there would resurrect the exact variable Jackal deliberately removes before handoff, silently defeating the gateway's pinned model; `ANTHROPIC_BASE_URL` would redirect gateway traffic — carrying the gateway's own `ANTHROPIC_AUTH_TOKEN` — to a different host; `ANTHROPIC_API_KEY` would hand a personal key to whoever runs the gateway; the `USE_` flags would switch the session to a different backend and abandon the gateway entirely.
- `apiKeyHelper` — Anthropic's own gateway guidance tells users to put this in `~/.claude/settings.json` to supply credentials. Gateway authentication always comes from the gateway's own token, never from the normal profile, so this key is dropped rather than allowed to override or conflict with it.

If stripping empties the `env` block entirely, the block itself is removed rather than left as `{}`. Jackal prints one line to stderr naming what was dropped and why, so the user is not left wondering why a setting they wrote isn't taking effect in a gateway session. The normal profile's file is never modified — only the in-memory copy that becomes the gateway's file.

### Links

Jackal creates a link for each entry of the normal profile that the gateway directory does not already have, skipping `settings.json`. New entries appearing later are linked on the next launch.

An entry that is already a link is left alone. An entry that is a real file or directory is a profile created by the fully isolated build: it is renamed aside with a `.jackal-isolated.bak` suffix and then linked, so those gateways start sharing without losing their old state. Nothing is deleted, and the rename happens once — afterwards the link is what exists. If the backup name is already taken, the entry is left as it is (reported once) rather than overwriting an earlier backup.

The rename and the link are made atomic by hand, per entry: if the link cannot be created, the rename is undone before moving on, restoring the entry exactly as it was under its original name. This matters because a symlink failure is typically systemic, not per-entry — a Windows install without Developer Mode refuses every symlink, not a random few — so without the rollback, every real entry in an upgrading gateway would be renamed aside and none of them relinked: strictly worse than before the upgrade, repeating on every launch. An entry only counts as migrated once the link that replaces it has actually succeeded. If the rollback itself fails, that is reported immediately and loudly, separate from the batched link-failure summary, because it is the one case where the entry really is at the `.bak` name and the user needs to know where to find it.

Where symbolic links cannot be created — notably Windows without Developer Mode or administrator rights, or a filesystem that refuses symlinks (FAT/exFAT, some network mounts, SELinux) — Jackal reports one actionable line and launches with whatever links exist. Model isolation does not depend on links, so the gateway still works, and — because of the rollback above — a pre-existing entry that failed to migrate is left exactly where it was, not stranded.

## Model source of truth

After migration, the gateway-local Claude `settings.json` is the persistent model source of truth.

The gateway `.env` remains responsible for connection and discovery configuration only:

```text
ANTHROPIC_BASE_URL=https://gateway.example
ANTHROPIC_AUTH_TOKEN=...
CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=0  # optional opt-out
```

`ANTHROPIC_MODEL` is removed from newly written gateway files and is not exported after migration.

An explicit `--model` argument remains a Claude-supported one-session override and is forwarded unchanged.

## New gateway setup

1. Prompt for gateway name, URL, and token as today.
2. Fetch the gateway's `/v1/models` catalogue as today.
3. Require a model selection rather than allowing a blank persistent default.
4. Accept either a catalogue number or a manually typed valid model ID.
5. Write URL and token to `~/.jackal/<name>.env` without `ANTHROPIC_MODEL`.
6. Create `~/.jackal/claude/<name>/` with restrictive permissions where supported.
7. Atomically seed the selected model into `~/.jackal/claude/<name>/settings.json`, preserving any unrelated keys if the file already exists.
8. Link the normal profile's entries into the gateway directory.
9. Launch Claude with the gateway-specific `CLAUDE_CONFIG_DIR`.

If the catalogue is unavailable or empty, setup still requires a manually typed model ID. A failed model fetch must not prevent saving a valid manually entered model.

## Existing gateway migration

Migration runs before Claude is launched.

### Gateway already has an isolated model

If `~/.jackal/claude/<name>/settings.json` contains a valid `model`, it is authoritative. Jackal must not overwrite it from a legacy `.env` value.

### Legacy gateway has `ANTHROPIC_MODEL`

If isolated settings do not yet contain a model:

1. Read and validate the legacy `ANTHROPIC_MODEL` value.
2. Atomically seed it into gateway-local `settings.json`.
3. Atomically rewrite the gateway `.env` without the obsolete line.
4. Preserve all unrelated gateway variables and file permissions.
5. Do not export the old variable.

This is a one-time seed. Native `/model` owns all subsequent changes.

### Legacy gateway has no model

An explicit selection is required before launch:

- With a controlling terminal, Jackal fetches the current gateway catalogue and shows the existing model picker. If the catalogue cannot be fetched or is empty, the user must type a valid model ID manually.
- Without a controlling terminal, Jackal exits immediately with a clear instruction to run the gateway interactively once.

Normal Claude's model is never considered as a migration source or fallback.

## Launch data flow

```text
jackal [--gateway NAME] [claude args...]
        |
        v
resolve gateway and isolated Claude directory
        |
        v
bootstrap/migrate gateway-local model if required
        |
        v
load gateway transport configuration
        |
        +--> ANTHROPIC_BASE_URL
        +--> ANTHROPIC_AUTH_TOKEN
        +--> discovery opt-out, if configured
        |
        v
link normal profile entries, except settings.json
rewrite gateway settings.json = normal settings + gateway model
        |
        v
force CLAUDE_CONFIG_DIR=~/.jackal/claude/NAME
remove inherited ANTHROPIC_MODEL
set discovery default with setdefault("1")
        |
        v
exec Claude with original forwarded args
```

Ordering matters:

1. Migration reads the gateway file before legacy model data can be discarded.
2. Gateway transport values are loaded.
3. The gateway model is read from the gateway `settings.json` before that file is rewritten, so a model persisted by native `/model` is never lost.
4. Links are created before launch, so an entry added to the normal profile since the last launch is visible in this one.
5. Jackal forces its own `CLAUDE_CONFIG_DIR`, so a gateway file or parent shell cannot redirect persistence elsewhere.
6. Jackal removes `ANTHROPIC_MODEL`, including inherited shell values and legacy gateway-file values.
7. An explicit forwarded `--model` remains intact in Claude's argument list.

## Native `/model` behavior

No interception is required.

For gateway `work`:

```text
/model
  Enter -> writes model to ~/.jackal/claude/work/settings.json
  s     -> changes only the running session
```

For gateway `personal`, Claude writes to a different directory. Ordinary `claude` continues using its normal user profile.

## Components and file ownership

### `jackal_lib/gateways.py`

Add the minimum helpers needed to:

- derive the gateway-specific Claude directory and settings path;
- read a valid model from isolated settings;
- atomically seed or update the model while preserving unrelated JSON keys;
- find and atomically remove a legacy `ANTHROPIC_MODEL` line;
- preserve gateway file permissions during migration;
- link the normal profile's entries into a gateway directory, skipping `settings.json` and any name already present;
- rewrite a gateway `settings.json` from the normal profile's settings plus a given model.

Do not introduce a general settings framework or profile abstraction.

### `jackal_lib/setup.py`

- Require a model choice.
- Continue using the existing catalogue and validation logic.
- Write only gateway transport data to `.env`.
- Seed the isolated Claude settings model.

### `jackal_lib/launch.py`

- Bootstrap or migrate the selected gateway before loading Claude.
- Link the normal profile and refresh the gateway `settings.json` before handoff.
- Force the gateway-specific `CLAUDE_CONFIG_DIR` after gateway configuration is loaded.
- Remove inherited `ANTHROPIC_MODEL` before handoff.
- Preserve model discovery behavior.
- Preserve POSIX `execv` and Windows exit-code propagation.

### `jackal_lib/models.py`

Reuse existing model fetching, rendering, and `usable_model` validation. If needed, make the existing picker support a required raw-ID fallback when no catalogue is available. Do not add a second picker implementation.

### `test.py`

Extend the existing fake Claude rather than adding a second framework. The fake may receive test-only arguments that emulate:

- reporting its resolved configuration directory and effective model;
- persistently changing the model in its active settings file;
- changing the model for one process without persistence;
- coordinating concurrent writes through condition/barrier files.

All state paths must remain under the test's temporary home.

### Documentation

Update README and configuration/design documentation to state:

- a gateway isolates the model and shares the rest of the Claude user profile;
- project-local Claude configuration still applies;
- user-level hooks, skills, agents, MCP configuration, permissions, plugins, history, and login state are shared live through links, so they behave exactly as in normal Claude;
- a non-model preference changed inside a Jackal session does not persist, because normal Claude owns it;
- setup and migration require a model;
- native `/model` Enter persists per gateway and **s** is session-only;
- `ANTHROPIC_MODEL` is a legacy storage format, not the new source of truth.

## Atomic writes

Settings and gateway-file migrations must use same-directory temporary files followed by `os.replace`.

Requirements:

1. Do not truncate the destination before the replacement is ready.
2. Preserve or restore restrictive permissions.
3. Remove temporary files on handled failure where possible.
4. Never overwrite malformed gateway-local JSON.
5. Preserve unrelated JSON keys and unrelated `.env` lines.

No cross-process lock is required across an interactive session. Different gateways have different files. Concurrent sessions using the same gateway intentionally share one preference and use normal last-writer-wins semantics.

## Error handling

### Malformed isolated settings

Exit with an error naming the exact file. Do not overwrite, delete, reset, or infer a replacement.

### Malformed normal Claude settings

Exit with an error naming the normal profile's file. Never repair it, and never fall back to a model-only gateway file: that would silently drop the user's `permissions` rules.

### Links cannot be created

Report one actionable line naming the entry and the reason, then continue. Model isolation does not depend on links. On Windows this usually means Developer Mode or administrator rights are required.

### Invalid stored model

Treat an invalid model value as unusable configuration and require an explicit selection. Do not pass unsafe text into an environment or terminal.

### Cannot create or write isolated configuration

Exit before launching Claude. Report the target path and underlying error in one line without exposing credentials.

### Legacy migration failure

The original gateway `.env` must remain intact. If isolated settings were successfully seeded but cleanup fails, a later launch must recognize the isolated model as authoritative and avoid overwriting it.

### Catalogue unavailable

In an interactive flow, require a manually typed model ID. In a headless flow, exit with an actionable command.

### Missing terminal

Never block. Existing configured gateways launch headlessly; only an unpinned gateway requiring bootstrap fails fast.

## Testing strategy

### New setup storage

Verify that:

- selected model is stored in gateway-local `settings.json`;
- `.env` has no `ANTHROPIC_MODEL`;
- files retain restrictive permissions;
- a blank selection is not accepted.

### Native persistent selection

Using the stateful fake Claude:

1. Seed normal Claude state with model A.
2. Launch Jackal gateway `work` and persist model B.
3. Reopen `work` and observe B.
4. Invoke fake Claude directly and observe A.
5. Assert normal state is byte-for-byte unchanged.

### Session-only selection

1. Persist gateway default B.
2. Run one session using temporary model C.
3. Verify that process reports C.
4. Reopen the gateway and observe B.

### Profile sharing

Verify that:

- an agent, plugin, rule, and `CLAUDE.md` in the normal profile are visible from a gateway;
- `.claude.json` resolves to the normal profile's file, so personal MCP servers and login state are shared;
- an entry added to the normal profile after the gateway was created is linked on the next launch;
- `settings.json` is a real file, never a link;
- a `permissions`, `hooks`, or `enabledPlugins` value set in the normal profile is present in the gateway file, while `model` is the gateway's;
- changing a non-model preference in the gateway file does not survive the next launch, and never reaches the normal profile;
- `env.ANTHROPIC_MODEL` in the normal profile's settings does not reach the gateway file or the launched process, while an unrelated `env` key survives;
- `env.ANTHROPIC_BASE_URL`, `env.ANTHROPIC_AUTH_TOKEN`, and `apiKeyHelper` are all dropped from the merge, with one line to stderr naming them;
- a gateway left over from the isolated build, holding a real `.claude.json` and `plugins/`, ends up with working links and its originals intact under `.jackal-isolated.bak` names;
- an entry that is already a link is left alone;
- when linking fails (symlink creation forced to error), a pre-existing real entry that was being migrated is left exactly where it started, under its original name, not stranded at the backup suffix;
- a malformed normal `settings.json` exits and names that file, leaving the gateway file unchanged.

### Gateway isolation

- Persist B for `work` and C for `personal`.
- Reopen both and verify their independent defaults.
- Run overlapping different-gateway writes using condition-based barriers rather than fixed sleeps.
- Assert neither gateway writes into the other's directory.

### Legacy pinned migration

Verify that:

- legacy `ANTHROPIC_MODEL=B` seeds gateway-local settings once;
- the obsolete line is removed atomically;
- unrelated `.env` lines and permissions survive;
- a later native selection C is not reset to B.

### Legacy unpinned migration

Verify that:

- interactive launch requires and saves a selection;
- catalogue failure falls back to required raw input;
- headless launch fails clearly;
- normal Claude's model is never copied.

### Environment containment

Verify that:

- inherited `ANTHROPIC_MODEL` does not reach Claude;
- inherited `CLAUDE_CONFIG_DIR` is replaced;
- explicit `--model` remains forwarded;
- discovery defaults to `1` and the gateway opt-out still wins.

### Project configuration

Create repository-local `.claude`, `.mcp.json`, and `CLAUDE.md` sentinels and verify the fake Claude can see them, independently of the shared user-level profile.

### Corruption and interruption safety

Verify that:

- malformed isolated JSON remains byte-for-byte unchanged;
- failed migration does not truncate the original `.env`;
- temporary files do not replace destinations before complete writes.

### Existing suite and platform coverage

- Keep all existing tests green.
- Run focused persistence tests repeatedly.
- Run the full suite on macOS, Linux, and Windows.
- Keep persistence tests non-PTY and cross-platform; only interactive picker tests retain the existing POSIX skip where necessary.

## Acceptance criteria

1. Persistent `/model` selection in one gateway does not alter normal Claude or another gateway.
2. Session-only model selection does not change that gateway's next launch.
3. New and migrated gateways have an explicit gateway-local default model.
4. Normal Claude user-profile files are never opened for mutation by Jackal.
5. Repository-local Claude configuration remains effective.
6. A Jackal session sees the same agents, skills, plugins, personal MCP servers, hooks, permissions, and login state as normal Claude, without copying them.
7. Legacy `ANTHROPIC_MODEL` values seed once and no longer override native persistence.
8. Inherited model/config-directory environment values cannot bypass isolation.
9. Failure paths preserve existing configuration and never expose tokens.
10. Existing process-handoff semantics and all unrelated behavior remain unchanged.

## Rejected alternatives

### Shared user profile plus session-only policy

Small, but Enter still changes normal Claude. It fails the required native persistent behavior.

### Fully isolated profiles

Isolating the entire user profile is the smallest change and the strongest guarantee, but it silently strips a gateway of everything the user configured: personal MCP servers, agents, plugins, hooks, permissions, global `CLAUDE.md`, and login state. Verified against Claude Code 2.1.220 — `claude mcp list` under a gateway `CLAUDE_CONFIG_DIR` reports no MCP servers at all. A gateway should change the model, not amputate the tool.

### Copying the normal profile into each gateway

Avoids links, so it works without Developer Mode on Windows, but every copy drifts the moment the normal profile changes, and reconciling the two directions is the synchronization problem this design exists to avoid. Only `settings.json` is copied, because its contents cannot be split any other way.

### Snapshot and restore normal settings

Races with concurrent Claude sessions, loses independent changes, leaves corruption after crashes, and requires replacing `execv` with a supervising wrapper.

### File watcher or post-write repair

Observes the unwanted global write only after it happens and cannot safely distinguish Jackal changes from normal Claude changes.

### `--settings`, `--setting-sources`, `--model`, or `ANTHROPIC_MODEL` alone

These affect loading or launch precedence. None changes where native `/model` persists its default.

### Wait for upstream field-level persistence

Would be cleaner if Claude Code adds a model-specific writable settings target, but it does not fix current releases.
