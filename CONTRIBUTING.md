# Contributing

`jackal` is pure-stdlib Python with no runtime and no dev dependencies. Clone
it, run the tests, and you have the full development environment.

```sh
git clone https://github.com/VladTemp27/jackal.git && cd jackal
npm link          # symlinks `jackal` onto your PATH
python3 test.py
```

`npm link` means edits to the repo take effect immediately, with no reinstall
step. `sys.path[0]` is the resolved script directory, so `jackal_lib` is
importable through that symlink.

## Layout

```
jackal                  entry point: argument dispatch and main()
jackal_lib/
  terminal.py           colour support, the controlling tty
  gateways.py           paths, naming, listing, migration, loading
  models.py             fetching /v1/models, the picker, id validation
  setup.py              the interactive --setup flow
  updates.py            the once-daily update check
  launch.py             the banner and the handoff to claude
test.py                 the whole suite, stdlib unittest
docs/                   configuration, design notes, troubleshooting
```

The dependency graph is a DAG and should stay one: `terminal` imports nothing
else of ours; `gateways` and `models` depend only on it; `setup`, `updates`, and
`launch` sit on top. Keeping it acyclic is what lets any single module be read
on its own.

## Tests

```sh
python3 test.py                                  # everything
python3 test.py JackalTest.test_version_reports_claude_too   # one test
```

Stdlib `unittest` and `pty`, no dev dependencies. Every test runs against a
throwaway `$HOME` with a stub `claude`, so it never touches your real config or
reaches a real gateway. The model-discovery tests stand up a stub gateway on
`127.0.0.1` with `http.server` — a real socket, since the suite drives `jackal`
as a subprocess and has nothing in-process to patch. The pty tests self-skip on
Windows.

Two conventions worth keeping:

- **The stub `claude` reports the token's *length*, never its value.** That way
  any literal token appearing in captured output is a real leak rather than the
  stub echoing it back.
- **The pty harness waits for each prompt before sending input**, rather than
  sleeping a fixed interval. A fixed sleep races the echo-disable: send the
  token before `getpass` takes over and the tty echoes it back, which looks
  exactly like a credential leak. That failure only appears under load, so it
  passes locally and breaks in CI.

## Lint

```sh
pipx run ruff==0.16.0 check jackal jackal_lib test.py
pipx run ruff==0.16.0 format --check jackal jackal_lib test.py
```

The version is pinned deliberately — an unpinned linter turns every new rule
into a red build on a commit that changed nothing.

## CI

Every push runs, across Linux, macOS, and Windows:

- the suite on Python 3.9 and 3.13
- `npm i -g .` followed by actually invoking `jackal`, which is what catches
  packaging regressions such as a module missing from the `files` array or a
  lost executable bit
- ruff check and format

## Releasing

Bump, tag, and publish together — the three drifting apart has caused real
confusion in this repo before, including two different builds published under
the same version number.

```sh
npm version minor          # edits package.json, commits, and tags atomically
git push origin main --follow-tags
npm publish --otp=<code>   # 2FA is enabled on the package
```

Then confirm the registry actually moved, since npmjs.com's page is a cached
render and lags behind:

```sh
npm view jackal-cli dist-tags
```

If a new file or directory is added, check it is covered by the `files` array
in `package.json` — `npm pack --dry-run` lists exactly what would ship. The
`npm-install` CI job exists to catch the case where it isn't.
