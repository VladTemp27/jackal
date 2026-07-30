#!/usr/bin/env python3
"""Test suite for jackal. Run: python3 test.py

Covers the paths with real failure modes — the tty guard, credential
preservation, and output cleanliness. Every test uses a throwaway $HOME and a
stub `claude`, so it never touches your real config or reaches any gateway.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
JACKAL = HERE / "jackal"
LAUNCHER = HERE / "bin" / "jackal.js"
PROMPT = "›".encode()
POSIX = os.name != "nt"
NODE = shutil.which("node")

if POSIX:
    import pty

# Fake interpreter bodies for the bin/jackal.js launcher tests below (POSIX
# only — see the skip note on those tests for why). Plain Python, run via a
# `#!{sys.executable}` shebang, so the same source doubles as the file itself.
STORE_STUB_BODY = (
    "import sys\n"
    'sys.stdout.write("Python was not found; run without arguments to install'
    ' from the Microsoft Store.\\n")\n'
    "sys.exit(7)\n"  # any non-zero code — never assert on the literal 9009
)
SILENT_OK_BODY = "import sys\nsys.exit(0)\n"  # exits clean, proves nothing


class JackalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        bindir = self.home / "bin"
        bindir.mkdir(parents=True)
        self._write_stub(bindir)
        self.cfg = self.home / ".jackal.env"

    @staticmethod
    def _write_stub(bindir):
        """A fake `claude` that echoes what it received.

        Reports the token's LENGTH, never its value — so any literal token in
        captured output is a real leak, not the stub echoing it back. Written
        in Python rather than sh so the same stub works on Windows.
        """
        body = (
            "import os, sys\n"
            "print('CLAUDE args=[%s] url=[%s] toklen=[%d]' % (\n"
            "    ' '.join(sys.argv[1:]),\n"
            "    os.environ.get('ANTHROPIC_BASE_URL', ''),\n"
            "    len(os.environ.get('ANTHROPIC_AUTH_TOKEN', '')),\n"
            "))\n"
        )
        if os.name == "nt":
            (bindir / "claude_stub.py").write_text(body)
            # .cmd so PATHEXT resolution finds it; absolute interpreter path so
            # it does not depend on python being on the stripped-down PATH.
            (bindir / "claude.cmd").write_text(
                f'@echo off\r\n"{sys.executable}" "%~dp0claude_stub.py" %*\r\n'
            )
        else:
            stub = bindir / "claude"
            stub.write_text(f"#!{sys.executable}\n{body}")
            stub.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _write_py_stub(bindir, name, body):
        """A fake `name` interpreter that runs `body`, ignoring its own argv.

        Good enough to answer the launcher's `-c PROBE` probe — it never
        needs to actually execute an arbitrary script in these tests, since
        a stub that fails the probe is never invoked a second time.

        POSIX only: bin/jackal.js calls spawnSync without `shell: true` (on
        purpose — it also passes user arguments, and shell:true there would
        be an injection hazard), and Node cannot exec a .bat/.cmd directly
        without a shell; it raises EINVAL instead of running it. A `.cmd`
        fake here would fail for that reason, not because it's a rejected
        stub, which proves nothing. See the skip note on the tests below.
        """
        stub = bindir / name
        stub.write_text(f"#!{sys.executable}\n{body}")
        stub.chmod(0o755)

    @staticmethod
    def _write_real_interpreter(bindir, name):
        """A genuinely working `name` interpreter: forwards to the real
        Python running this test, so the launcher's sentinel probe passes
        and it can go on to actually execute `jackal`. POSIX only, for the
        same reason as _write_py_stub — a Windows equivalent would need to
        be a real .exe, not a .cmd.
        """
        (bindir / name).symlink_to(sys.executable)

    def _shadow_all_candidates(self, bindir, body):
        """Shadow python3/python/py with a rejectable stub.

        Needed for the "no usable interpreter" tests: some machines have a
        real `py` (or `python`) sitting on PATH already, which would
        otherwise make the launcher succeed for real and defeat the test.
        """
        for name in ("python3", "python", "py"):
            self._write_py_stub(bindir, name, body)

    def _set_claude_exit_code(self, code):
        """Overwrite the setUp-installed `claude` stub to just exit(code)."""
        bindir = self.home / "bin"
        body = f"import sys\nsys.exit({code})\n"
        if os.name == "nt":
            (bindir / "claude_stub.py").write_text(body)
        else:
            stub = bindir / "claude"
            stub.write_text(f"#!{sys.executable}\n{body}")
            stub.chmod(0o755)

    @staticmethod
    def _system_path():
        """Minimal PATH that is guaranteed not to contain a real `claude`."""
        if os.name == "nt":
            root = os.environ.get("SYSTEMROOT", r"C:\Windows")
            return [os.path.join(root, "System32"), root]
        return ["/usr/bin", "/bin"]

    def env(self, with_claude=True):
        dirs = self._system_path()
        if with_claude:
            dirs.insert(0, str(self.home / "bin"))
        e = {
            "HOME": str(self.home),
            "PATH": os.pathsep.join(dirs),
            "TERM": "xterm-256color",
        }
        if os.name == "nt":
            # Path.home() reads USERPROFILE on Windows, not HOME. And Windows
            # Python cannot even start without SYSTEMROOT — it needs the crypto
            # API to seed hash randomisation. PATHEXT is what makes the .cmd
            # stub resolvable by name.
            e["USERPROFILE"] = str(self.home)
            for k in ("SYSTEMROOT", "PATHEXT", "COMSPEC", "TEMP", "TMP"):
                if k in os.environ:
                    e[k] = os.environ[k]
        return e

    def seed(self, url, token):
        self.cfg.write_text(f"ANTHROPIC_BASE_URL={url}\nANTHROPIC_AUTH_TOKEN={token}\n")
        self.cfg.chmod(0o600)

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

    def run_piped(self, *args, with_claude=True):
        """Run with stdout as a pipe — i.e. not a tty."""
        return subprocess.run(
            [sys.executable, str(JACKAL), *args],
            env=self.env(with_claude),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=60,  # a prompt that blocks should fail the suite, not stall CI
        )

    def run_launcher(self, *args):
        """Run bin/jackal.js the way npm's shim would: `jackal <args>`."""
        return subprocess.run(
            [NODE, str(LAUNCHER), *args],
            env=self.env(),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=60,
        )

    @staticmethod
    def _read(fd):
        """b'' on no-data-yet, None at EOF."""
        try:
            chunk = os.read(fd, 65536)
            return chunk if chunk else None
        except BlockingIOError:
            return b""
        except OSError:
            return None

    def run_pty(self, inputs=(), args=()):
        """Drive jackal through a real terminal, answering each prompt.

        Waits for the Nth prompt glyph before sending line N. A fixed sleep
        races the echo-disable: send the token before getpass takes over and
        the tty echoes it back, which looks exactly like a credential leak.
        That failure only appears under load, so it passes locally and breaks
        in CI.
        """
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(str(JACKAL), [str(JACKAL), *args], self.env())
            os._exit(127)  # unreachable unless execve fails
        os.set_blocking(fd, False)
        buf = b""
        for i, line in enumerate(inputs):
            deadline = time.time() + 15
            while buf.count(PROMPT) < i + 1 and time.time() < deadline:
                chunk = self._read(fd)
                if chunk is None:
                    break
                buf += chunk
                if not chunk:
                    time.sleep(0.02)
            os.write(fd, (line + "\n").encode())
        deadline = time.time() + 15
        while time.time() < deadline:
            chunk = self._read(fd)
            if chunk is None:
                break
            buf += chunk
            if not chunk:
                time.sleep(0.02)
        _, status = os.waitpid(pid, 0)
        return buf.decode(errors="replace"), os.waitstatus_to_exitcode(status)

    # -- tests ------------------------------------------------------------

    def test_headless_without_config_fails_fast(self):
        """No tty and no config must exit, not hang waiting on input."""
        r = self.run_piped()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("need a terminal", r.stdout + r.stderr)

    def test_packaging_shebang_and_exec_bit(self):
        """npm links this file directly; a lost exec bit breaks every install."""
        self.assertTrue(os.access(JACKAL, os.X_OK), "jackal must be executable")
        self.assertTrue(JACKAL.read_text().startswith("#!"), "jackal needs a shebang")

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_bad_name_writes_nothing(self):
        """A name that isn't safe as a filename component must be rejected."""
        self.run_pty(inputs=["../evil"])
        self.assertFalse(
            (self.home / ".jackal").exists(), "no directory should be created"
        )
        self.assertFalse(
            (self.home / "evil.env").exists(), "no file should land outside ~/.jackal"
        )

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
        self.seed(
            "https://keep.test", "tok_keep"
        )  # old flat file, migrates to "default"
        self.run_pty(inputs=["default", "ftp://bad"], args=["--setup"])
        self.assertIn(
            "https://keep.test", (self.home / ".jackal" / "default.env").read_text()
        )

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_setup_with_default_already_set_leaves_current_unchanged(self):
        self.seed_named("work", "https://work.test", "tok_w")
        self.set_current("work")
        self.run_pty(
            inputs=["personal", "https://personal.test", "tok_p"],
            args=["--setup", "--version"],
        )
        self.assertEqual(
            (self.home / ".jackal" / "current").read_text().strip(), "work"
        )

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_banner_on_tty_only(self):
        self.seed(
            "https://banner.test", "tok_b"
        )  # old flat file, migrates to "default"
        out, _ = self.run_pty(args=["-p", "hi"])
        self.assertIn("default", out, "banner must name the active gateway")
        self.assertIn("banner.test", out)
        self.assertNotIn("tok_b", out, "banner must never print the token")

    def test_banner_suppressed_when_piped(self):
        """Keeps `jackal -p ... > file` machine-readable."""
        self.seed("https://banner.test", "tok_b")
        r = self.run_piped("-p", "hi")
        self.assertNotIn("jackal ·", r.stdout)

    def test_args_and_env_forwarded(self):
        self.seed("https://banner.test", "tok_b")
        r = self.run_piped("-p", "hi")
        self.assertIn(
            "CLAUDE args=[-p hi] url=[https://banner.test] toklen=[5]", r.stdout
        )

    def test_missing_claude_suggests_install(self):
        self.seed("https://x.test", "t")
        r = self.run_piped("-p", "hi", with_claude=False)
        self.assertIn("npm i -g @anthropic-ai/claude-code", r.stdout + r.stderr)

    def test_migrates_old_flat_config(self):
        self.seed("https://old.test", "tok_old")
        (self.home / ".jackal.env").chmod(0o644)
        self.run_piped("-p", "hi")
        self.assertFalse(
            (self.home / ".jackal.env").exists(), "old file must be moved, not copied"
        )
        gw = self.home / ".jackal" / "default.env"
        self.assertTrue(gw.exists())
        if POSIX:
            # Windows chmod only toggles the read-only attribute (no real
            # POSIX mode bits), so stat().st_mode never reads back as 0600
            # there — this assertion is meaningful on POSIX only.
            self.assertEqual(gw.stat().st_mode & 0o777, 0o600)
        self.assertIn("https://old.test", gw.read_text())
        self.assertEqual(
            (self.home / ".jackal" / "current").read_text().strip(), "default"
        )

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

    def test_use_sets_default(self):
        self.seed_named("work", "https://work.test", "tok_w")
        self.seed_named("personal", "https://personal.test", "tok_p")
        self.set_current("work")
        r = self.run_piped("use", "personal")
        self.assertEqual(r.returncode, 0)
        self.assertIn("personal", r.stdout)
        self.assertEqual(
            (self.home / ".jackal" / "current").read_text().strip(), "personal"
        )

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

    def test_gateway_flag_overrides_default_without_changing_it(self):
        self.seed_named("work", "https://work.test", "tok_w")
        self.seed_named("personal", "https://personal.test", "tok_p")
        self.set_current("work")
        r = self.run_piped("--gateway", "personal", "-p", "hi")
        self.assertIn("url=[https://personal.test]", r.stdout)
        self.assertEqual(
            (self.home / ".jackal" / "current").read_text().strip(), "work"
        )

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
        self.assertIn(
            "url=[https://work.test]", r.stdout
        )  # the sole/default gateway, not overridden
        self.assertIn("args=[-p hi --gateway work]", r.stdout)

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
        self.assertEqual(
            (self.home / ".jackal" / "current").read_text().strip(), "work"
        )

    def test_remove_unknown_gateway_errors(self):
        self.seed_named("work", "https://work.test", "tok_w")
        r = self.run_piped("--remove", "ghost")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no gateway named", r.stdout + r.stderr)

    def test_remove_requires_name(self):
        r = self.run_piped("--remove")
        self.assertNotEqual(r.returncode, 0)

    def test_remove_rejects_traversal_name(self):
        self.seed_named("work", "https://work.test", "tok_w")
        victim = self.home / "victim.env"
        victim.write_text(
            "ANTHROPIC_BASE_URL=https://evil.test\nANTHROPIC_AUTH_TOKEN=tok_e\n"
        )
        r = self.run_piped("--remove", "../victim")
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(
            victim.exists(), "traversal must not delete files outside ~/.jackal"
        )

    def test_use_rejects_traversal_name(self):
        self.seed_named("work", "https://work.test", "tok_w")
        victim = self.home / "victim.env"
        victim.write_text(
            "ANTHROPIC_BASE_URL=https://evil.test\nANTHROPIC_AUTH_TOKEN=tok_e\n"
        )
        r = self.run_piped("use", "../victim")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.home / ".jackal" / "current").exists())

    def test_gateway_flag_rejects_traversal_name(self):
        self.seed_named("work", "https://work.test", "tok_w")
        victim = self.home / "victim.env"
        victim.write_text(
            "ANTHROPIC_BASE_URL=https://evil.test\nANTHROPIC_AUTH_TOKEN=tok_e\n"
        )
        r = self.run_piped("--gateway", "../victim", "-p", "hi")
        self.assertNotEqual(r.returncode, 0)

    # -- bin/jackal.js (the npm launcher) ----------------------------------
    #
    # npm's Windows cmd-shim generator used to copy the shebang interpreter
    # name (`python3`) verbatim and do a bare PATH lookup for it — which the
    # Microsoft Store's app-execution-alias stub shadows. bin/jackal.js
    # replaces that with its own probe. These tests drive the real launcher
    # through `node`, with fake interpreters standing in for the stub, a
    # working Python, and a Python-shaped no-op — never the tester's own
    # Python resolution, so the outcome doesn't depend on what's installed.
    #
    # POSIX only. The fakes below are plain files with a `#!interpreter`
    # shebang; Node's spawnSync (no `shell: true`, deliberately, to avoid an
    # argument-injection hazard on the real run) cannot execute a Windows
    # .bat/.cmd the same way — it raises EINVAL rather than running it, so a
    # `.cmd` stand-in would fail the probe for the wrong reason and prove
    # nothing about the launcher's stub-detection logic. Getting a real fake
    # interpreter on Windows needs an actual compiled .exe; the Windows leg
    # of CI covers that separately with a genuine npm-installed shim instead.
    WINDOWS_SKIP_REASON = (
        "Windows fakes would need a compiled .exe: spawnSync without "
        "shell:true raises EINVAL on a .cmd, not the behavior under test — "
        "covered separately in CI"
    )

    @unittest.skipUnless(NODE, "node not on PATH")
    @unittest.skipIf(os.name == "nt", WINDOWS_SKIP_REASON)
    def test_launcher_propagates_nonzero_exit_from_child(self):
        """A real interpreter on PATH: the exit code jackal (via claude)
        produces must come back unchanged, not swallowed or replaced."""
        self.seed("https://gw.test", "tok")
        self._write_real_interpreter(self.home / "bin", "python3")
        self._set_claude_exit_code(17)
        r = self.run_launcher("-p", "hi")
        self.assertEqual(r.returncode, 17)

    @unittest.skipUnless(NODE, "node not on PATH")
    @unittest.skipIf(os.name == "nt", WINDOWS_SKIP_REASON)
    def test_launcher_skips_store_stub_and_uses_next_candidate(self):
        """`python3` resolves to the Store alias stub; the launcher must
        recognize it, not accept it, and fall through to `python`."""
        self.seed("https://gw.test", "tok")
        bindir = self.home / "bin"
        self._write_py_stub(bindir, "python3", STORE_STUB_BODY)
        self._write_real_interpreter(bindir, "python")
        self._set_claude_exit_code(0)
        r = self.run_launcher("-p", "hi")
        combined = r.stdout + r.stderr
        # Wrongly accepting the stub would re-invoke it for the real run too,
        # printing its message and exiting with its (non-zero) code — the
        # opposite of what a working fallback to `python` looks like.
        self.assertEqual(r.returncode, 0, combined)
        self.assertNotIn("Microsoft Store", combined)

    @unittest.skipUnless(NODE, "node not on PATH")
    @unittest.skipIf(os.name == "nt", WINDOWS_SKIP_REASON)
    def test_launcher_reports_store_alias_hint_when_stub_seen(self):
        """No candidate works and a Store stub was seen along the way:
        exit 1, pointing at the app-execution-alias setting, not a
        generic "go install Python" message."""
        bindir = self.home / "bin"
        self._shadow_all_candidates(bindir, STORE_STUB_BODY)
        r = self.run_launcher()
        combined = r.stdout + r.stderr
        self.assertEqual(r.returncode, 1)
        self.assertIn("Microsoft Store", combined)
        self.assertIn("execution alias", combined.lower())

    @unittest.skipUnless(NODE, "node not on PATH")
    @unittest.skipIf(os.name == "nt", WINDOWS_SKIP_REASON)
    def test_launcher_rejects_silent_exit_and_reports_plain_install_message(self):
        """A candidate that exits 0 without ever printing the sentinel must
        not be trusted on exit code alone. With nothing else usable, the
        launcher must still fail outright — and since no stub was ever
        seen, point at the plain python.org link instead of the Store."""
        bindir = self.home / "bin"
        self._shadow_all_candidates(bindir, SILENT_OK_BODY)
        r = self.run_launcher()
        combined = r.stdout + r.stderr
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Microsoft Store", combined)
        self.assertIn("python.org", combined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
