# Multi-Gateway Support for jackal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `jackal` save multiple named gateways and switch between them, instead of always using the single gateway in `~/.jackal.env`.

**Architecture:** `jackal` is a single dependency-free Python script (`jackal`) plus a black-box test suite (`test.py`) that only ever spawns the script as a subprocess (real terminal via `pty` for interactive prompts, plain pipes for everything else) against a throwaway `$HOME` and a stub `claude`. This plan keeps that shape: no new files, no new dependencies, no unit-testing of internal functions in isolation — every new test drives the script end-to-end exactly like the existing tests do. `~/.jackal.env` (one flat file) is replaced by `~/.jackal/<name>.env` (one file per gateway, same `KEY=VALUE` body) plus `~/.jackal/current` (one line naming the default). An old flat file is migrated to `~/.jackal/default.env` automatically and silently.

**Tech Stack:** Python 3.9+ stdlib only (`getpass`, `os`, `re`, `shutil`, `subprocess`, `sys`, `pathlib`). `unittest` + `pty` for tests, same as today.

## Global Constraints

- No new files and no new dependencies — everything lives in the existing `jackal` script and `test.py`.
- Every credential file (`~/.jackal/<name>.env`) is written at `0600`, same as today's `~/.jackal.env`.
- Gateway names must match `^[A-Za-z0-9_-]+$` and be non-empty — they become part of a file path, so this is a trust-boundary check (rejects `../` traversal, empty names, etc).
- `jackal` only inspects `args[0]` (and `args[1]` for two-word commands) to decide whether to intercept — everything else always passes straight through to `claude` untouched. This mirrors the existing `--setup`/`--reconfigure` handling and keeps argument parsing trivial (no argparse, no new dependency).
- Never guess which gateway to use when it's ambiguous (2+ gateways, no default set) — error out and tell the user to run `jackal use <name>`.
- The token must never be printed or logged, in any new code path (`--list` prints host only, same as the banner already does).
- Spec: `docs/superpowers/specs/2026-07-29-multi-gateway-design.md`.

---

### Task 1: Storage layer, name-prompting setup, migration, and default-gateway dispatch

**Files:**
- Modify: `jackal` (whole file — constants, `run_setup`, `load_config`, `banner`, `main`)
- Test: `test.py`

**Interfaces:**
- Produces (used by every later task): `JACKAL_DIR: Path`, `CURRENT_FILE: Path`, `valid_name(name: str) -> bool`, `gateway_path(name: str) -> Path`, `list_gateways() -> list[str]`, `read_current() -> str | None`, `write_current(name: str) -> None`, `_host(url: str) -> str`, `launch(name: str, args: list[str]) -> None` (loads config, prints banner, execs/spawns `claude` — never returns on POSIX, exits the process on all platforms).

- [ ] **Step 1: Write the failing tests**

Add to `test.py`, inside `JackalTest`. First, two new helpers alongside `seed`:

```python
    def seed_named(self, name, url, token):
        """Write a new-format gateway file directly, bypassing interactive setup."""
        gdir = self.home / ".jackal"
        gdir.mkdir(exist_ok=True)
        path = gdir / f"{name}.env"
        path.write_text(f"ANTHROPIC_BASE_URL={url}\nANTHROPIC_AUTH_TOKEN={token}\n")
        path.chmod(0o600)
        return path

    def set_current(self, name):
        gdir = self.home / ".jackal"
        gdir.mkdir(exist_ok=True)
        (gdir / "current").write_text(name + "\n")
```

Then replace the bodies of these four existing tests (same names, same decorators):

```python
    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_bad_name_writes_nothing(self):
        """A name that isn't safe as a filename component must be rejected."""
        self.run_pty(inputs=["../evil"])
        self.assertFalse((self.home / ".jackal").exists(), "no directory should be created")

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_bad_url_writes_nothing(self):
        self.run_pty(inputs=["work", "ftp://nope"])
        self.assertFalse((self.home / ".jackal" / "work.env").exists())

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_intake_writes_0600_and_hides_token(self):
        out, _ = self.run_pty(
            inputs=["testgw", "https://gw.test", "tok_abc123"], args=["--version"]
        )
        gw = self.home / ".jackal" / "testgw.env"
        self.assertTrue(gw.exists())
        self.assertEqual(gw.stat().st_mode & 0o777, 0o600)
        body = gw.read_text()
        self.assertIn("ANTHROPIC_BASE_URL=https://gw.test", body)
        self.assertIn("tok_abc123", body)
        self.assertNotIn("tok_abc123", out, "token must never reach the screen")

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_aborted_setup_preserves_config(self):
        """A failed reconfigure must not destroy working credentials."""
        self.seed("https://keep.test", "tok_keep")  # old flat file, migrates to "default"
        self.run_pty(inputs=["default", "ftp://bad"], args=["--setup"])
        self.assertIn("https://keep.test", (self.home / ".jackal" / "default.env").read_text())

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_banner_on_tty_only(self):
        self.seed("https://banner.test", "tok_b")  # old flat file, migrates to "default"
        out, _ = self.run_pty(args=["-p", "hi"])
        self.assertIn("default", out, "banner must name the active gateway")
        self.assertIn("banner.test", out)
        self.assertNotIn("tok_b", out, "banner must never print the token")
```

Now add these new tests (non-interactive, no pty needed):

```python
    def test_migrates_old_flat_config(self):
        self.seed("https://old.test", "tok_old")
        self.run_piped("-p", "hi")
        self.assertFalse((self.home / ".jackal.env").exists(), "old file must be moved, not copied")
        gw = self.home / ".jackal" / "default.env"
        self.assertTrue(gw.exists())
        self.assertEqual(gw.stat().st_mode & 0o777, 0o600)
        self.assertIn("https://old.test", gw.read_text())
        self.assertEqual((self.home / ".jackal" / "current").read_text().strip(), "default")

    def test_single_gateway_auto_used_without_default(self):
        self.seed_named("work", "https://work.test", "tok_w")
        r = self.run_piped("-p", "hi")
        self.assertIn("url=[https://work.test]", r.stdout)

    def test_stale_current_falls_back(self):
        """A current file naming a gateway that no longer exists is treated as unset."""
        self.seed_named("work", "https://work.test", "tok_w")
        self.set_current("ghost")
        r = self.run_piped("-p", "hi")
        self.assertIn("url=[https://work.test]", r.stdout)

    def test_ambiguous_default_errors_without_use(self):
        self.seed_named("work", "https://work.test", "tok_w")
        self.seed_named("personal", "https://personal.test", "tok_p")
        r = self.run_piped("-p", "hi")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("jackal use", r.stdout + r.stderr)
        self.assertIn("work", r.stdout + r.stderr)
        self.assertIn("personal", r.stdout + r.stderr)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 test.py -v`
Expected: the new/updated tests fail (old ones still reference the flat-file behavior that no longer matches the code, and `seed_named`/`set_current`/migration/dispatch don't exist yet). `test_packaging_shebang_and_exec_bit` and the still-unmodified tests continue to pass.

- [ ] **Step 3: Rewrite the constants, terminal-independent storage helpers, setup flow, config loading, banner, and dispatch**

Replace the top of `jackal` (module docstring through `CLAUDE_HINT`):

```python
#!/usr/bin/env python3
"""jackal — launch Claude Code against a custom Anthropic gateway.

Gateways live under ~/.jackal/<name>.env (0600). Manage them with:
  jackal --setup            add or edit a gateway (prompts for its name)
  jackal use <name>         set the default gateway
  jackal --gateway <name>   launch against one gateway without changing the default
  jackal --list             show saved gateways
  jackal --remove <name>    delete a saved gateway
"""

import getpass
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

JACKAL_DIR = Path.home() / ".jackal"
CURRENT_FILE = JACKAL_DIR / "current"
OLD_CFG = Path.home() / ".jackal.env"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
CLAUDE_HINT = "install it with: npm i -g @anthropic-ai/claude-code"
```

Leave `colors()`, `_enable_vt()`, `_stdin_is_console()`, and `open_tty()` exactly as they are.

Add a new section right after `open_tty()` and before the `# setup` section comment:

```python
# --------------------------------------------------------------------------
# gateway storage


def valid_name(name):
    """True if name is safe to use as a filename component."""
    return bool(name) and bool(NAME_RE.match(name))


def gateway_path(name):
    return JACKAL_DIR / f"{name}.env"


def list_gateways():
    """Sorted names of all saved gateways."""
    if not JACKAL_DIR.is_dir():
        return []
    return sorted(p.stem for p in JACKAL_DIR.glob("*.env"))


def read_current():
    """The default gateway's name, or None if unset or it no longer exists."""
    if not CURRENT_FILE.is_file():
        return None
    name = CURRENT_FILE.read_text().strip()
    return name if name and gateway_path(name).is_file() else None


def write_current(name):
    JACKAL_DIR.mkdir(exist_ok=True)
    CURRENT_FILE.write_text(name + "\n")


def migrate_old_config():
    """Move a pre-multi-gateway ~/.jackal.env into ~/.jackal/default.env."""
    if not OLD_CFG.is_file() or list_gateways():
        return
    JACKAL_DIR.mkdir(exist_ok=True)
    OLD_CFG.rename(gateway_path("default"))
    write_current("default")
```

Replace the whole `run_setup` function with:

```python
def run_setup(out, tty_in):
    """Prompt for a gateway name, URL, and token; write it to disk.

    Returns the saved gateway's name.
    """
    c = colors()

    def w(s):
        out.write(s)
        out.flush()

    def die(msg):
        w(f"\n  {c['R']}✗{c['Z']}  {msg}\n\n")
        sys.exit(1)

    w(f"\n  {c['C']}╭────────────────────────────────────────╮{c['Z']}\n")
    w(
        f"  {c['C']}│{c['Z']}  {c['B']}jackal{c['Z']}  ·  Claude via custom gateway  {c['C']}│{c['Z']}\n"
    )
    w(f"  {c['C']}╰────────────────────────────────────────╯{c['Z']}\n\n")

    w(f"  {c['C']}▸{c['Z']} {c['B']}Gateway name{c['Z']}\n    {c['D']}›{c['Z']} ")
    name = (tty_in.readline() or "").strip()
    if not valid_name(name):
        die("gateway name must be non-empty and only letters, digits, - or _ — nothing saved")

    path = gateway_path(name)
    short = str(path).replace(str(Path.home()), "~", 1)
    w(f"\n  {c['D']}writing to{c['Z']} {short}\n\n")

    w(f"  {c['C']}▸{c['Z']} {c['B']}Anthropic base URL{c['Z']}\n    {c['D']}›{c['Z']} ")
    url = (tty_in.readline() or "").strip()
    if not url.startswith(("http://", "https://")):
        die("URL must start with http:// or https:// — nothing saved")

    w(
        f"\n  {c['C']}▸{c['Z']} {c['B']}Auth token{c['Z']}   {c['D']}input hidden{c['Z']}\n"
    )
    # getpass reads from the terminal directly and restores echo even on
    # KeyboardInterrupt; it is the portable equivalent of stty -echo.
    token = getpass.getpass(f"    {c['D']}›{c['Z']} ", stream=out).strip()
    if not token:
        die("token required — nothing saved")

    JACKAL_DIR.mkdir(exist_ok=True)
    # O_CREAT with mode 0600 means the file is never briefly world-readable.
    # chmod afterwards also covers overwriting a pre-existing file.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"ANTHROPIC_BASE_URL={url}\nANTHROPIC_AUTH_TOKEN={token}\n")
    os.chmod(path, 0o600)

    if read_current() is None:
        write_current(name)

    w(
        f"\n\n  {c['G']}✓{c['Z']}  saved gateway \"{name}\"  {c['D']}(0600, {len(token)} chars){c['Z']}\n\n"
    )
    return name
```

Replace `load_config` (it now takes an explicit path instead of always reading the module-level `CFG`):

```python
def load_config(path):
    """Read KEY=VALUE lines from path into the environment."""
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip()
```

Replace `banner` (it now takes the active gateway's name so multiple similarly-hosted gateways stay distinguishable). This also introduces `_host()`, a tiny shared helper `print_list()` reuses in Task 4 instead of duplicating the same parsing:

```python
def _host(url):
    """The bare host from a base URL, e.g. https://gw.test/v1 -> gw.test."""
    return url.split("://")[-1].split("/")[0]


def banner(name):
    """One line naming the active gateway. Never the token, never when piped."""
    if not sys.stdout.isatty():
        return
    c = colors()
    host = _host(os.environ.get("ANTHROPIC_BASE_URL", ""))
    if c["C"]:
        print(
            f"\n  {c['C']}◆{c['Z']} {c['B']}jackal{c['Z']} {c['D']}· gateway{c['Z']} "
            f"{c['C']}{name}{c['Z']} {c['D']}·{c['Z']} {c['C']}{host}{c['Z']}\n"
        )
    else:
        print(f"\n  jackal · gateway {name} · {host}\n")
```

Finally, replace `main()` (keep the `if __name__ == "__main__":` block below it untouched):

```python
def launch(name, args):
    """Load a gateway's config, show the banner, and hand off to claude."""
    load_config(gateway_path(name))
    banner(name)

    claude = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    if not os.access(claude, os.X_OK):
        sys.exit(
            f"jackal: Claude Code not found (looked for '{claude}')\n        {CLAUDE_HINT}"
        )

    if os.name == "nt":
        # Windows has no execve. os.execv there spawns a detached process and
        # exits immediately, so the shell returns before Claude finishes.
        # Wait explicitly and propagate the exit code instead.
        sys.exit(subprocess.run([claude, *args], check=False).returncode)
    os.execv(claude, [claude, *args])  # replaces this process entirely


def _prompt_setup(args):
    tty_in, tty_out = open_tty()
    if tty_in is None:
        sys.exit("jackal: need a terminal to configure a gateway")
    try:
        name = run_setup(tty_out, tty_in)
    finally:
        tty_in.close()
        if tty_out is not tty_in:
            tty_out.close()
    launch(name, args)


def main():
    args = sys.argv[1:]
    migrate_old_config()

    if args and args[0] in ("--setup", "--reconfigure"):
        _prompt_setup(args[1:])
        return

    names = list_gateways()
    if not names:
        _prompt_setup(args)
        return

    current = read_current()
    if current is None:
        if len(names) == 1:
            current = names[0]
        else:
            sys.exit(
                "jackal: multiple gateways saved, no default set — run `jackal use <name>`\n"
                "        saved gateways: " + ", ".join(names)
            )
    launch(current, args)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 test.py -v`
Expected: PASS for every test, including all the ones untouched by this task (`test_banner_on_tty_only`, `test_banner_suppressed_when_piped`, `test_args_and_env_forwarded`, `test_missing_claude_suggests_install`, `test_headless_without_config_fails_fast`, `test_packaging_shebang_and_exec_bit`).

- [ ] **Step 5: Commit**

```bash
git add jackal test.py
git commit -m "Store gateways under ~/.jackal/<name>.env, migrate the old flat config"
```

---

### Task 2: `jackal use <name>` — switch the default gateway

**Files:**
- Modify: `jackal` (`main()`)
- Test: `test.py`

**Interfaces:**
- Consumes: `gateway_path`, `write_current`, `list_gateways` (Task 1).

- [ ] **Step 1: Write the failing tests**

Add to `test.py`:

```python
    def test_use_sets_default(self):
        self.seed_named("work", "https://work.test", "tok_w")
        self.seed_named("personal", "https://personal.test", "tok_p")
        self.set_current("work")
        r = self.run_piped("use", "personal")
        self.assertEqual(r.returncode, 0)
        self.assertIn("personal", r.stdout)
        self.assertEqual((self.home / ".jackal" / "current").read_text().strip(), "personal")

    def test_use_then_bare_launches_chosen_gateway(self):
        self.seed_named("work", "https://work.test", "tok_w")
        self.seed_named("personal", "https://personal.test", "tok_p")
        self.run_piped("use", "personal")
        r = self.run_piped("-p", "hi")
        self.assertIn("url=[https://personal.test]", r.stdout)

    def test_use_unknown_gateway_errors(self):
        self.seed_named("work", "https://work.test", "tok_w")
        r = self.run_piped("use", "ghost")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no gateway named", r.stdout + r.stderr)
        self.assertIn("--list", r.stdout + r.stderr)

    def test_use_requires_name(self):
        r = self.run_piped("use")
        self.assertNotEqual(r.returncode, 0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 test.py -v`
Expected: FAIL — `use` isn't recognized yet, so these fall through to the ambiguous-default / auto-use / setup paths instead of doing what the tests expect.

- [ ] **Step 3: Add the `use` dispatch to `main()`**

In `jackal`, inside `main()`, insert this block right after the `--setup`/`--reconfigure` block and before `names = list_gateways()`:

```python
    if args and args[0] == "use":
        if len(args) < 2:
            sys.exit("jackal: use requires a gateway name, e.g. jackal use work")
        name = args[1]
        if not gateway_path(name).is_file():
            sys.exit(f"jackal: no gateway named '{name}' — see jackal --list")
        write_current(name)
        print(f"  ✓  default gateway is now \"{name}\"")
        return
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 test.py -v`
Expected: PASS, including all Task 1 tests (no regressions).

- [ ] **Step 5: Commit**

```bash
git add jackal test.py
git commit -m "Add jackal use <name> to switch the default gateway"
```

---

### Task 3: `jackal --gateway <name>` — one-off override

**Files:**
- Modify: `jackal` (`main()`)
- Test: `test.py`

**Interfaces:**
- Consumes: `gateway_path`, `launch`, `read_current` (Task 1).

- [ ] **Step 1: Write the failing tests**

Add to `test.py`:

```python
    def test_gateway_flag_overrides_default_without_changing_it(self):
        self.seed_named("work", "https://work.test", "tok_w")
        self.seed_named("personal", "https://personal.test", "tok_p")
        self.set_current("work")
        r = self.run_piped("--gateway", "personal", "-p", "hi")
        self.assertIn("url=[https://personal.test]", r.stdout)
        self.assertEqual((self.home / ".jackal" / "current").read_text().strip(), "work")

    def test_gateway_flag_unknown_name_errors(self):
        self.seed_named("work", "https://work.test", "tok_w")
        r = self.run_piped("--gateway", "ghost", "-p", "hi")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no gateway named", r.stdout + r.stderr)

    def test_gateway_flag_requires_name(self):
        r = self.run_piped("--gateway")
        self.assertNotEqual(r.returncode, 0)

    def test_gateway_flag_only_recognized_at_front(self):
        """--gateway must be args[0] — elsewhere it's just forwarded to claude untouched."""
        self.seed_named("work", "https://work.test", "tok_w")
        r = self.run_piped("-p", "hi", "--gateway", "work")
        self.assertIn("url=[https://work.test]", r.stdout)  # the sole/default gateway, not overridden
        self.assertIn("args=[-p hi --gateway work]", r.stdout)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 test.py -v`
Expected: FAIL — `--gateway` isn't recognized yet.

- [ ] **Step 3: Add the `--gateway` dispatch to `main()`**

In `jackal`, inside `main()`, insert this block right after the `use` block from Task 2:

```python
    if args and args[0] == "--gateway":
        if len(args) < 2:
            sys.exit("jackal: --gateway requires a gateway name, e.g. jackal --gateway work")
        name = args[1]
        if not gateway_path(name).is_file():
            sys.exit(f"jackal: no gateway named '{name}' — see jackal --list")
        launch(name, args[2:])
        return
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 test.py -v`
Expected: PASS, including all Task 1 and Task 2 tests.

- [ ] **Step 5: Commit**

```bash
git add jackal test.py
git commit -m "Add jackal --gateway <name> for one-off launches"
```

---

### Task 4: `jackal --list` — show saved gateways

**Files:**
- Modify: `jackal` (`main()`, new `print_list()`)
- Test: `test.py`

**Interfaces:**
- Consumes: `list_gateways`, `read_current`, `gateway_path`, `_host`, `colors` (Task 1 and pre-existing).

- [ ] **Step 1: Write the failing tests**

Add to `test.py`:

```python
    def test_list_shows_all_gateways_and_marks_default(self):
        self.seed_named("work", "https://work.test", "tok_w")
        self.seed_named("personal", "https://personal.test", "tok_p")
        self.set_current("work")
        r = self.run_piped("--list")
        self.assertEqual(r.returncode, 0)
        self.assertIn("work", r.stdout)
        self.assertIn("personal", r.stdout)
        self.assertIn("work.test", r.stdout)
        self.assertIn("personal.test", r.stdout)
        self.assertIn("default", r.stdout)
        self.assertNotIn("tok_w", r.stdout)
        self.assertNotIn("tok_p", r.stdout)

    def test_list_when_empty(self):
        r = self.run_piped("--list")
        self.assertEqual(r.returncode, 0)
        self.assertIn("no gateways saved", r.stdout)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 test.py -v`
Expected: FAIL — `--list` isn't recognized yet (falls through to the setup/dispatch path instead).

- [ ] **Step 3: Add `print_list()` and its dispatch**

In `jackal`, add this function right after `banner`. It reuses the `_host()` helper Task 1 added alongside `banner()` rather than re-parsing the URL inline:

```python
def print_list():
    names = list_gateways()
    if not names:
        print("  no gateways saved — run: jackal --setup")
        return
    current = read_current()
    c = colors()
    for name in names:
        url = ""
        for line in gateway_path(name).read_text().splitlines():
            if line.startswith("ANTHROPIC_BASE_URL="):
                url = line.partition("=")[2].strip()
        mark = f"  {c['G']}(default){c['Z']}" if name == current else ""
        print(f"  {c['C']}▸{c['Z']} {name}{mark}   {c['D']}{_host(url)}{c['Z']}")
```

In `main()`, insert this block right after the `--gateway` block from Task 3:

```python
    if args and args[0] == "--list":
        print_list()
        return
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 test.py -v`
Expected: PASS, including all earlier tasks' tests.

- [ ] **Step 5: Commit**

```bash
git add jackal test.py
git commit -m "Add jackal --list to show saved gateways"
```

---

### Task 5: `jackal --remove <name>` — delete a saved gateway

**Files:**
- Modify: `jackal` (`main()`)
- Test: `test.py`

**Interfaces:**
- Consumes: `gateway_path`, `read_current`, `CURRENT_FILE` (Task 1).

- [ ] **Step 1: Write the failing tests**

Add to `test.py`:

```python
    def test_remove_deletes_gateway_file(self):
        self.seed_named("work", "https://work.test", "tok_w")
        self.seed_named("personal", "https://personal.test", "tok_p")
        r = self.run_piped("--remove", "personal")
        self.assertEqual(r.returncode, 0)
        self.assertFalse((self.home / ".jackal" / "personal.env").exists())
        self.assertTrue((self.home / ".jackal" / "work.env").exists())

    def test_remove_default_clears_current(self):
        self.seed_named("work", "https://work.test", "tok_w")
        self.seed_named("personal", "https://personal.test", "tok_p")
        self.set_current("personal")
        self.run_piped("--remove", "personal")
        self.assertFalse((self.home / ".jackal" / "current").exists())
        # exactly one gateway remains, so bare jackal auto-uses it (Task 1 rule)
        r = self.run_piped("-p", "hi")
        self.assertIn("url=[https://work.test]", r.stdout)

    def test_remove_non_default_leaves_current_untouched(self):
        self.seed_named("work", "https://work.test", "tok_w")
        self.seed_named("personal", "https://personal.test", "tok_p")
        self.set_current("work")
        self.run_piped("--remove", "personal")
        self.assertEqual((self.home / ".jackal" / "current").read_text().strip(), "work")

    def test_remove_unknown_gateway_errors(self):
        self.seed_named("work", "https://work.test", "tok_w")
        r = self.run_piped("--remove", "ghost")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no gateway named", r.stdout + r.stderr)

    def test_remove_requires_name(self):
        r = self.run_piped("--remove")
        self.assertNotEqual(r.returncode, 0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 test.py -v`
Expected: FAIL — `--remove` isn't recognized yet.

- [ ] **Step 3: Add the `--remove` dispatch to `main()`**

In `jackal`, inside `main()`, insert this block right after the `--list` block from Task 4:

```python
    if args and args[0] == "--remove":
        if len(args) < 2:
            sys.exit("jackal: --remove requires a gateway name, e.g. jackal --remove work")
        name = args[1]
        path = gateway_path(name)
        if not path.is_file():
            sys.exit(f"jackal: no gateway named '{name}' — see jackal --list")
        was_current = read_current() == name
        path.unlink()
        if was_current:
            CURRENT_FILE.unlink(missing_ok=True)
        print(f"  ✓  removed gateway \"{name}\"")
        return
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 test.py -v`
Expected: PASS — full suite green.

- [ ] **Step 5: Commit**

```bash
git add jackal test.py
git commit -m "Add jackal --remove <name> to delete a saved gateway"
```

---

### Task 6: Update README for multi-gateway usage

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the "Use" section**

In `README.md`, replace the existing "Use" section's fenced example block:

```
jackal                  # first run prompts for URL + token, then launches
jackal -p "hello"       # all arguments forward to claude untouched
jackal --setup          # change the URL/token later (--reconfigure also works)
```

with:

```
jackal                       # launches against the default gateway
jackal -p "hello"            # all arguments forward to claude untouched
jackal --setup                # add a new gateway or edit an existing one (prompts for its name)
jackal use work               # switch the default gateway to "work"
jackal --gateway work -p "hi" # one-off launch against "work" without changing the default
jackal --list                 # show saved gateways, marking the default
jackal --remove work          # delete a saved gateway
```

Directly below it, add a short paragraph:

```
The first gateway you set up automatically becomes the default. Adding more
gateways with `jackal --setup` never changes the default on its own — switch
it explicitly with `jackal use <name>`. If you only ever save one gateway,
`jackal` just uses it; if you save more than one and never pick a default,
`jackal` refuses to guess and tells you to run `jackal use <name>`.
```

- [ ] **Step 2: Update the "Uninstall" section**

Replace:

```
npm un -g jackal-cli    # remove the command
rm ~/.jackal.env        # remove the stored gateway URL and token
```

with:

```
npm un -g jackal-cli    # remove the command
rm -rf ~/.jackal        # remove every stored gateway URL and token
```

And update the sentence below it from "`npm un` removes the binary but leaves `~/.jackal.env` behind" to "`npm un` removes the binary but leaves `~/.jackal/` behind".

- [ ] **Step 3: Update "How it works"**

Add a short paragraph before the existing `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` table:

```
Gateways are stored one-per-file under `~/.jackal/<name>.env` (same `0600`
permissions as before), with `~/.jackal/current` naming the default. A
pre-existing `~/.jackal.env` from an older version of jackal is migrated
automatically, once, into a gateway named `default`.
```

- [ ] **Step 4: Update the Security section's file path**

Replace `~/.jackal.env` with `~/.jackal/` in the "Security" section's opening sentence, keeping the rest of that section's wording.

- [ ] **Step 5: Run the full test suite one more time**

Run: `python3 test.py -v`
Expected: PASS — confirms the docs task introduced no code regressions (it shouldn't have touched `jackal` or `test.py` at all).

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "Document multi-gateway usage in README"
```
