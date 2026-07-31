#!/usr/bin/env python3
"""Test suite for jackal. Run: python3 test.py

Covers the paths with real failure modes — the tty guard, credential
preservation, output cleanliness, and what a gateway can talk jackal into
writing. Every test uses a throwaway $HOME and a stub `claude`, so it never
touches your real config. Gateway responses come from a stub on 127.0.0.1,
so nothing leaves the machine and no real gateway is contacted.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
JACKAL = HERE / "jackal"
PROMPT = "›".encode()
POSIX = os.name != "nt"

# Two pages, so pagination is exercised rather than assumed. last_id is the
# final entry's id, as a real gateway reports it.
PAGE1 = {
    "data": [
        {"id": "gw-one", "display_name": "Gateway One"},
        {"id": "gw-two", "display_name": "Gateway Two"},
    ],
    "first_id": "gw-one",
    "has_more": True,
    "last_id": "gw-two",
}
PAGE2 = {
    "data": [{"id": "gw-three", "display_name": "Gateway Three"}],
    "first_id": "gw-three",
    "has_more": False,
    "last_id": None,
}
# A gateway is trusted with your traffic, not with your config file. An id
# holding a newline would append a second KEY=value line that load_config reads
# back as a real variable — here redirecting the base URL, and the token with
# it, on every later launch.
HOSTILE = {
    "data": [
        {
            "id": "evil\nANTHROPIC_BASE_URL=https://attacker.test",
            "display_name": "Looks Legitimate",
        }
    ],
    "has_more": False,
    "last_id": None,
}

if POSIX:
    import pty


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
            "print('CLAUDE args=[%s] url=[%s] toklen=[%d] model=[%s] discovery=[%s]'\n"
            "      % (\n"
            "    ' '.join(sys.argv[1:]),\n"
            "    os.environ.get('ANTHROPIC_BASE_URL', ''),\n"
            "    len(os.environ.get('ANTHROPIC_AUTH_TOKEN', '')),\n"
            "    os.environ.get('ANTHROPIC_MODEL', ''),\n"
            "    os.environ.get('CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY', ''),\n"
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

    def models_server(self, status=200, pages=None, expect_auth=None):
        """A localhost /v1/models. Returns (base_url, seen).

        A real socket, not a monkeypatch: the suite runs jackal as a
        subprocess, so there is nothing in-process to patch. seen collects
        (path, auth_matched) per request, which is how the Bearer header and
        the pagination cursor get asserted.
        """
        seen = []
        pages = pages if pages is not None else {None: PAGE1, "gw-two": PAGE2}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                # Path and a bool, never the token itself: the suite's leak
                # detector is "a literal token in captured output", and an
                # assertEqual failure message printing seen would break it.
                seen.append(
                    (self.path, self.headers.get("Authorization") == expect_auth)
                )
                if status != 200:
                    self.send_error(status)
                    return
                after = parse_qs(urlparse(self.path).query).get("after_id", [None])[0]
                payload = pages.get(after, {"data": [], "has_more": False})
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass  # keep the suite's output readable

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}", seen

    def truncated_server(self):
        """A gateway whose body is shorter than its declared Content-Length."""

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "100000")
                self.end_headers()
                self.wfile.write(b'{"data": [')  # then hang up mid-object

            def log_message(self, *args):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def garbage_status_server(self):
        """A port that answers, but not with HTTP — the wrong port, basically.

        http.client raises BadStatusLine here, an HTTPException that is NOT an
        OSError, so it escapes the obvious except clause and takes --setup down
        with a traceback unless it is named. A raw socket, because
        BaseHTTPRequestHandler cannot produce a malformed status line.
        """
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(5)

        def serve():
            while True:
                try:
                    conn, _ = srv.accept()
                except OSError:
                    return
                conn.recv(65536)
                conn.sendall(b"this is not a status line\r\n\r\n")
                conn.close()

        threading.Thread(target=serve, daemon=True).start()
        self.addCleanup(srv.close)
        return f"http://127.0.0.1:{srv.getsockname()[1]}"

    @staticmethod
    def dead_url():
        """A URL nothing is listening on — bind for a port, then drop it."""
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return f"http://127.0.0.1:{s.getsockname()[1]}"

    def gateway_body(self, name="testgw"):
        return (self.home / ".jackal" / f"{name}.env").read_text()

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
            try:
                os.write(fd, (line + "\n").encode())
            except OSError:
                # jackal rejected an earlier answer and exited, so the pty is
                # gone. Stop feeding it: the test wants to assert on what was
                # written to disk, not die with EIO here.
                break
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

    def test_version_matches_package_json(self):
        """--version must report the version npm actually published."""
        expected = json.loads((HERE / "package.json").read_text())["version"]
        r = self.run_piped("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn(f"jackal {expected}", r.stdout)
        self.assertNotIn("unknown", r.stdout)

    def test_version_works_without_any_gateway(self):
        """Asking the version must not depend on config state."""
        r = self.run_piped("--version", with_claude=False)
        self.assertEqual(r.returncode, 0)
        self.assertRegex(r.stdout, r"jackal \d+\.\d+\.\d+")
        # No gateway is configured here, so the setup prompt must not appear.
        self.assertNotIn("need a terminal", r.stdout + r.stderr)

    def test_version_reports_claude_too(self):
        """Intercepting --version must not cost the user claude's version."""
        r = self.run_piped("--version")
        self.assertIn("claude ", r.stdout)

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
            inputs=["testgw", "https://gw.test", "tok_abc123"], args=[]
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

    # -- model discovery --------------------------------------------------

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_picker_number_writes_that_model(self):
        url, _ = self.models_server()
        out, _ = self.run_pty(inputs=["testgw", url, "tok_abc123", "2"], args=[])
        self.assertIn("ANTHROPIC_MODEL=gw-two\n", self.gateway_body())
        self.assertIn("Gateway Two", out, "picker must render display_name")
        self.assertNotIn("tok_abc123", out, "token must never reach the screen")

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_picker_accepts_raw_model_id(self):
        """An advertised list can be a subset — an unlisted id must still work."""
        url, _ = self.models_server()
        self.run_pty(inputs=["testgw", url, "tok_a", "claude-haiku-4-5"], args=[])
        self.assertIn("ANTHROPIC_MODEL=claude-haiku-4-5\n", self.gateway_body())

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_blank_writes_no_model_pin(self):
        """Skipping must leave Claude Code's own default alone."""
        url, _ = self.models_server()
        self.run_pty(inputs=["testgw", url, "tok_a", ""], args=[])
        body = self.gateway_body()
        self.assertIn("ANTHROPIC_BASE_URL=", body)
        self.assertNotIn("ANTHROPIC_MODEL", body)

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_fetch_sends_bearer_token(self):
        """Must match the header claude sends, or a passing fetch proves nothing."""
        url, seen = self.models_server(expect_auth="Bearer tok_abc123")
        self.run_pty(inputs=["testgw", url, "tok_abc123", ""], args=[])
        self.assertTrue(seen, "gateway was never asked for its models")
        self.assertTrue(seen[0][1], "Authorization was not 'Bearer <token>'")
        self.assertTrue(seen[0][0].startswith("/v1/models"), seen[0][0])

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_pagination_reaches_second_page(self):
        url, seen = self.models_server()
        self.run_pty(inputs=["testgw", url, "tok_a", "3"], args=[])
        self.assertIn("ANTHROPIC_MODEL=gw-three\n", self.gateway_body())
        self.assertEqual(len(seen), 2, "should stop after has_more goes false")
        self.assertIn("after_id=gw-two", seen[1][0])

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_missing_models_endpoint_still_saves(self):
        """A gateway serving only /v1/messages is supported, not an error."""
        url, _ = self.models_server(status=404)
        out, code = self.run_pty(inputs=["testgw", url, "tok_a"], args=[])
        body = self.gateway_body()
        self.assertIn(f"ANTHROPIC_BASE_URL={url}", body)
        self.assertIn("tok_a", body)
        self.assertNotIn("ANTHROPIC_MODEL", body)
        # "HTTP 404", not "404": the pty echoes the typed URL, so a random
        # ephemeral port containing 404 would pass a looser assertion.
        self.assertIn("HTTP 404", out, "the skip should say why")
        self.assertEqual(code, 0, "a missing endpoint must not fail setup")

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_unreachable_gateway_still_saves(self):
        out, code = self.run_pty(inputs=["testgw", self.dead_url(), "tok_a"], args=[])
        self.assertIn("tok_a", self.gateway_body())
        self.assertNotIn("ANTHROPIC_MODEL", self.gateway_body())
        self.assertIn("no model pinned", out)
        self.assertEqual(code, 0)

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_setup_over_existing_gateway_replaces_the_pin(self):
        """--setup rewrites a gateway wholesale, so a stale pin does not linger.

        Pins the rule deliberately: the file is a plain env file, so anyone who
        wants to keep an old pin through a failed fetch can edit it back.
        """
        path = self.seed_named("work", "https://old.test", "tok_old")
        path.write_text(path.read_text() + "ANTHROPIC_MODEL=stale-model\n")
        self.run_pty(
            inputs=["work", self.dead_url(), "tok_new"], args=["--setup", "--version"]
        )
        body = self.gateway_body("work")
        self.assertIn("tok_new", body)
        self.assertNotIn("stale-model", body)

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_truncated_response_still_saves(self):
        """IncompleteRead is not an OSError; it must not escape and kill setup."""
        out, code = self.run_pty(
            inputs=["testgw", self.truncated_server(), "tok_a"], args=[]
        )
        self.assertIn("tok_a", self.gateway_body())
        self.assertIn("no model pinned", out)
        self.assertNotIn("Traceback", out)
        self.assertEqual(code, 0)

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_garbage_status_line_still_saves(self):
        """Pointing jackal at a non-HTTP port must warn, not crash."""
        out, code = self.run_pty(
            inputs=["testgw", self.garbage_status_server(), "tok_a"],
            args=[],
        )
        self.assertIn("tok_a", self.gateway_body())
        self.assertNotIn("ANTHROPIC_MODEL", self.gateway_body())
        self.assertIn("BadStatusLine", out)
        self.assertNotIn("Traceback", out)
        self.assertEqual(code, 0)

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_oversized_response_still_saves(self):
        """A body too big to hold in memory is refused, not swallowed."""
        big = {
            "data": [{"id": f"m-{i}", "display_name": "x" * 200} for i in range(20000)],
            "has_more": False,
            "last_id": None,
        }
        url, _ = self.models_server(pages={None: big})
        out, code = self.run_pty(inputs=["testgw", url, "tok_a"], args=[])
        self.assertIn("tok_a", self.gateway_body())
        self.assertNotIn("ANTHROPIC_MODEL", self.gateway_body())
        self.assertIn("larger than", out)
        self.assertEqual(code, 0)

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_hostile_model_id_cannot_inject_an_env_line(self):
        """A gateway must not be able to append its own variable to the file.

        Caught at fetch time, so the entry is never offered at all — the
        picker showing an option that would be refused after picking it is a
        worse experience than not showing it.
        """
        url, _ = self.models_server(pages={None: HOSTILE})
        out, code = self.run_pty(inputs=["testgw", url, "tok_a"], args=[])
        body = self.gateway_body()
        self.assertNotIn("attacker.test", body, "second env line was written")
        self.assertNotIn("ANTHROPIC_MODEL", body)
        self.assertEqual(
            [ln for ln in body.splitlines() if ln.strip()],
            [f"ANTHROPIC_BASE_URL={url}", "ANTHROPIC_AUTH_TOKEN=tok_a"],
        )
        self.assertIn("listed no models", out, "hostile entry should be dropped")
        self.assertEqual(code, 0, "a bad id must not fail setup")

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_typed_model_id_with_equals_is_refused(self):
        """The fetch-time filter can't see a typed id, so the write-time gate stands."""
        url, _ = self.models_server()
        out, code = self.run_pty(inputs=["testgw", url, "tok_a", "bad=id"], args=[])
        body = self.gateway_body()
        self.assertNotIn("ANTHROPIC_MODEL", body)
        self.assertNotIn("bad=id", body)
        self.assertIn("can't be stored safely", out)
        self.assertEqual(code, 0)

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_superscript_digit_does_not_crash_picker(self):
        """isdigit() accepts characters int() rejects; '²' is next to 1 on AZERTY."""
        url, _ = self.models_server()
        out, code = self.run_pty(inputs=["testgw", url, "tok_a", "²"], args=[])
        self.assertNotIn("Traceback", out)
        self.assertEqual(code, 0)
        self.assertIn("tok_a", self.gateway_body(), "credentials must still be saved")

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_out_of_range_number_is_not_treated_as_a_model_id(self):
        """Typing 12 with 3 entries is a typo, not a model called "12"."""
        url, _ = self.models_server()
        out, _ = self.run_pty(inputs=["testgw", url, "tok_a", "12"], args=[])
        self.assertNotIn("ANTHROPIC_MODEL", self.gateway_body())
        self.assertIn("no entry 12", out)

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_hostile_id_is_dropped_before_it_reaches_the_terminal(self):
        """An id is printed next to display_name, so it needs the same gate.

        The escapes here would redraw the row above and make a decoy entry
        look like a legitimate model the user then picks.
        """
        pages = {
            None: {
                "data": [
                    {"id": "attacker-model", "display_name": "Claude Opus 4.6"},
                    {
                        "id": "x\033[1A\033[2K    1  Claude Opus 4.6   claude-opus-4-6",
                        "display_name": "decoy",
                    },
                ],
                "has_more": False,
                "last_id": None,
            }
        }
        url, _ = self.models_server(pages=pages)
        out, code = self.run_pty(inputs=["testgw", url, "tok_a", "2"], args=[])
        self.assertNotIn("\033[1A", out, "escape sequence reached the terminal")
        self.assertNotIn("\033[2K", out)
        # The hostile row is dropped entirely, so entry 2 no longer exists.
        self.assertIn("no entry 2", out)
        self.assertNotIn("ANTHROPIC_MODEL", self.gateway_body())
        self.assertEqual(code, 0)

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_lone_surrogate_in_id_does_not_crash_setup(self):
        """json accepts \\ud800; writing it to a utf-8 stream would raise."""
        pages = {
            None: {
                "data": [
                    {"id": "claude-\ud800-opus", "display_name": "Surrogate"},
                    {"id": "gw-one", "display_name": "Fine"},
                ],
                "has_more": False,
                "last_id": None,
            }
        }
        url, _ = self.models_server(pages=pages)
        out, code = self.run_pty(inputs=["testgw", url, "tok_a", "1"], args=[])
        self.assertNotIn("Traceback", out)
        self.assertEqual(code, 0)
        # The surrogate entry is gone, so entry 1 is the healthy one.
        self.assertIn("ANTHROPIC_MODEL=gw-one\n", self.gateway_body())

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_deeply_nested_json_does_not_crash_setup(self):
        """RecursionError is a RuntimeError, so no OSError/ValueError tuple caught it."""

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'{"data": ' + b"[" * 200000 + b"]" * 200000 + b"}"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        out, code = self.run_pty(inputs=["testgw", url, "tok_a"], args=[])
        self.assertNotIn("Traceback", out)
        self.assertIn("tok_a", self.gateway_body())
        self.assertEqual(code, 0)

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_empty_catalogue_says_so(self):
        """200 with an empty data[] must not look like a skipped prompt."""
        pages = {None: {"data": [], "has_more": False, "last_id": None}}
        url, _ = self.models_server(pages=pages)
        out, code = self.run_pty(inputs=["testgw", url, "tok_a"], args=[])
        self.assertIn("listed no models", out)
        self.assertNotIn("ANTHROPIC_MODEL", self.gateway_body())
        self.assertEqual(code, 0)

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_never_touches_claude_config_dir(self):
        """jackal's stated promise: it affects jackal alone."""
        url, _ = self.models_server()
        self.run_pty(inputs=["testgw", url, "tok_a", "1"], args=[])
        self.assertFalse((self.home / ".claude").exists())
        self.assertFalse((self.home / ".claude.json").exists())

    @unittest.skipUnless(POSIX, "pty is POSIX-only")
    def test_control_characters_stripped_from_display_name(self):
        """Gateway text reaching a terminal must not be able to redraw it."""
        pages = {
            None: {
                "data": [{"id": "gw-one", "display_name": "Safe\r\033[2KSpoofed"}],
                "has_more": False,
                "last_id": None,
            }
        }
        url, _ = self.models_server(pages=pages)
        out, _ = self.run_pty(inputs=["testgw", url, "tok_a", "1"], args=[])
        self.assertIn("ANTHROPIC_MODEL=gw-one\n", self.gateway_body())
        # The ESC byte and the \r are gone, so what is left renders as inert
        # text: the terminal prints "[2K" instead of erasing the line.
        self.assertIn("Safe[2KSpoofed", out)
        self.assertNotIn("\033[2K", out, "escape sequence reached the terminal")
        self.assertNotIn("Safe\r", out, "carriage return reached the terminal")

    def test_discovery_flag_reaches_claude(self):
        self.seed_named("work", "https://work.test", "tok_w")
        r = self.run_piped("-p", "hi")
        self.assertIn("discovery=[1]", r.stdout)

    def test_gateway_file_overrides_discovery_flag(self):
        """setdefault after load_config is the opt-out; prove the file wins."""
        path = self.seed_named("work", "https://work.test", "tok_w")
        path.write_text(
            path.read_text() + "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=0\n"
        )
        r = self.run_piped("-p", "hi")
        self.assertIn("discovery=[0]", r.stdout)

    def test_launch_forwards_saved_model(self):
        path = self.seed_named("work", "https://work.test", "tok_w")
        path.write_text(path.read_text() + "ANTHROPIC_MODEL=gw-one\n")
        r = self.run_piped("-p", "hi")
        self.assertIn("model=[gw-one]", r.stdout)

    def test_no_fetch_on_launch(self):
        """Setup-time only — launching must add no network round-trip."""
        url, seen = self.models_server()
        self.seed_named("work", url, "tok_w")
        self.run_piped("-p", "hi")
        self.assertEqual(seen, [], "launch must not call the gateway")

    def test_gateway_flag_rejects_traversal_name(self):
        self.seed_named("work", "https://work.test", "tok_w")
        victim = self.home / "victim.env"
        victim.write_text(
            "ANTHROPIC_BASE_URL=https://evil.test\nANTHROPIC_AUTH_TOKEN=tok_e\n"
        )
        r = self.run_piped("--gateway", "../victim", "-p", "hi")
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
