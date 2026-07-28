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
PROMPT = "›".encode()
POSIX = os.name != "nt"

if POSIX:
    import pty


class JackalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        (self.home / "bin").mkdir(parents=True)
        stub = self.home / "bin" / "claude"
        # Reports the token's LENGTH, never its value — so any literal token in
        # captured output is a real leak, not the stub echoing it back.
        stub.write_text(
            "#!/bin/sh\n"
            'echo "CLAUDE args=[$*] url=[$ANTHROPIC_BASE_URL]'
            ' toklen=[${#ANTHROPIC_AUTH_TOKEN}]"\n'
        )
        stub.chmod(0o755)
        self.cfg = self.home / ".jackal.env"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ----------------------------------------------------------

    def env(self, with_claude=True):
        path = f"{self.home / 'bin'}:/usr/bin:/bin" if with_claude else "/usr/bin:/bin"
        return {"HOME": str(self.home), "PATH": path, "TERM": "xterm-256color"}

    def seed(self, url, token):
        self.cfg.write_text(f"ANTHROPIC_BASE_URL={url}\nANTHROPIC_AUTH_TOKEN={token}\n")
        self.cfg.chmod(0o600)

    def run_piped(self, *args, with_claude=True):
        """Run with stdout as a pipe — i.e. not a tty."""
        return subprocess.run(
            [sys.executable, str(JACKAL), *args],
            env=self.env(with_claude),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
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
    def test_bad_url_writes_nothing(self):
        self.run_pty(inputs=["ftp://nope"])
        self.assertFalse(self.cfg.exists(), "a rejected URL must not write a config")

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_intake_writes_0600_and_hides_token(self):
        out, _ = self.run_pty(
            inputs=["https://gw.test", "tok_abc123"], args=["--version"]
        )
        self.assertTrue(self.cfg.exists())
        self.assertEqual(self.cfg.stat().st_mode & 0o777, 0o600)
        body = self.cfg.read_text()
        self.assertIn("ANTHROPIC_BASE_URL=https://gw.test", body)
        self.assertIn("tok_abc123", body)
        self.assertNotIn("tok_abc123", out, "token must never reach the screen")

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_aborted_setup_preserves_config(self):
        """A failed reconfigure must not destroy working credentials."""
        self.seed("https://keep.test", "tok_keep")
        self.run_pty(inputs=["ftp://bad"], args=["--setup"])
        self.assertIn("https://keep.test", self.cfg.read_text())

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_banner_on_tty_only(self):
        self.seed("https://banner.test", "tok_b")
        out, _ = self.run_pty(args=["-p", "hi"])
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
