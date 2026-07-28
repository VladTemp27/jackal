#!/bin/sh
# Test suite for jackal. Run: ./test.sh   (or TEST_SH=/bin/dash ./test.sh)
#
# Covers the paths with real failure modes — the tty guard, credential
# preservation, and output cleanliness. Uses a throwaway $HOME and a stub
# `claude`, so it never touches your real config or reaches any gateway.
set -eu

JACKAL="$(cd "$(dirname "$0")" && pwd)/jackal"
SH="${TEST_SH:-/bin/sh}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT INT TERM

pass=0; fail=0
ok()  { pass=$((pass + 1)); printf '  ok    %s\n' "$1"; }
bad() { fail=$((fail + 1)); printf '  FAIL  %s\n' "$1"; }
# assert <name> <cmd...> — passes when cmd succeeds; refute is the inverse.
assert() { n=$1; shift; if "$@" >/dev/null 2>&1; then ok "$n"; else bad "$n"; fi; }
refute() { n=$1; shift; if "$@" >/dev/null 2>&1; then bad "$n"; else ok "$n"; fi; }

cat > "$tmp/pty.py" <<'PY'
import os, pty, sys, time

# Waits for the Nth prompt glyph before sending each line. A fixed sleep races
# `stty -echo`: send the token before the script disables echo and the tty
# echoes it back, which looks exactly like a credential leak. Passes locally,
# fails under CI load.
PROMPT = "›".encode()

home = sys.argv[1]
sep = sys.argv.index("--")
inputs, args = sys.argv[2:sep], sys.argv[sep+1:]

pid, fd = os.forkpty()
if pid == 0:
    os.execve(os.environ["JACKAL_SH"],
              [os.environ["JACKAL_SH"], os.environ["JACKAL_BIN"]] + args,
              {"HOME": home, "PATH": home + "/bin:/usr/bin:/bin",
               "TERM": "xterm-256color"})

os.set_blocking(fd, False)
buf = b""

for i, line in enumerate(inputs):
    deadline = time.time() + 15
    while buf.count(PROMPT) < i + 1 and time.time() < deadline:
        try:
            c = os.read(fd, 65536)
            if c:
                buf += c
                continue
        except (BlockingIOError, OSError):
            pass
        time.sleep(0.02)
    os.write(fd, (line + "\n").encode())

deadline = time.time() + 15
while time.time() < deadline:
    try:
        c = os.read(fd, 65536)
        if c:
            buf += c
            continue
        break
    except BlockingIOError:
        time.sleep(0.02)
    except OSError:
        break

_, status = os.waitpid(pid, 0)
sys.stdout.write(buf.decode(errors="replace"))
sys.stderr.write("EXIT=%d\n" % os.waitstatus_to_exitcode(status))
PY

newhome() {
  h="$tmp/h$1"; mkdir -p "$h/bin"
  # Reports the token's LENGTH, never its value — so any literal token in the
  # captured output is a real leak rather than the stub echoing it back.
  # shellcheck disable=SC2016
  printf '#!/bin/sh\necho "CLAUDE args=[$*] url=[$ANTHROPIC_BASE_URL] toklen=[${#ANTHROPIC_AUTH_TOKEN}]"\n' > "$h/bin/claude"
  chmod +x "$h/bin/claude"
  echo "$h"
}
seed()   { printf 'ANTHROPIC_BASE_URL=%s\nANTHROPIC_AUTH_TOKEN=%s\n' "$2" "$3" > "$1/.jackal.env"; chmod 600 "$1/.jackal.env"; }
pty()    { JACKAL_SH="$SH" JACKAL_BIN="$JACKAL" python3 "$tmp/pty.py" "$@"; }
run()    { HOME="$1" PATH="$1/bin:/usr/bin:/bin" "$SH" "$JACKAL" "$2" "$3" </dev/null; }
is600()  { [ -n "$(find "$1" -perm 600 2>/dev/null)" ]; }

printf '\njackal test suite (shell: %s)\n\n' "$SH"

# 1. No tty and no config must fail fast, not hang. `test -r /dev/tty` passes
#    even with no controlling terminal, so this guards a real past bug.
h=$(newhome 1)
refute "headless without config exits nonzero" run "$h" -p hi
HOME="$h" PATH="$h/bin:/usr/bin:/bin" "$SH" "$JACKAL" </dev/null >"$tmp/o1" 2>&1 || true
assert "headless prints actionable error" grep -q "need a terminal" "$tmp/o1"

# 2. A malformed URL is rejected and nothing is written.
h=$(newhome 2)
pty "$h" "ftp://nope" -- >"$tmp/o2" 2>&1 || true
refute "bad URL writes no config" test -f "$h/.jackal.env"

# 3. Full intake writes 0600 with the right values, and never shows the token.
h=$(newhome 3)
pty "$h" "https://gw.test" "tok_abc123" -- --version >"$tmp/o3" 2>&1 || true
assert "intake writes 0600"        is600 "$h/.jackal.env"
assert "intake stores URL"         grep -q 'ANTHROPIC_BASE_URL=https://gw.test' "$h/.jackal.env"
assert "intake stores token"       grep -q 'tok_abc123' "$h/.jackal.env"
refute "token never echoed"        grep -q 'tok_abc123' "$tmp/o3"

# 4. An aborted --setup must not destroy a working config.
h=$(newhome 4); seed "$h" "https://keep.test" "tok_keep"
pty "$h" "ftp://bad" -- --setup >"$tmp/o4" 2>&1 || true
assert "aborted --setup preserves config" grep -q 'https://keep.test' "$h/.jackal.env"

# 5. Banner on a tty, absent when piped (keeps `-p` output machine-readable).
h=$(newhome 5); seed "$h" "https://banner.test" "tok_b"
pty "$h" -- -p hi >"$tmp/o5" 2>&1 || true
run "$h" -p hi >"$tmp/o5b" 2>&1
assert "banner shown on a tty"        grep -q 'banner.test' "$tmp/o5"
refute "banner suppressed when piped" grep -q 'jackal ·' "$tmp/o5b"
refute "banner never prints token"    grep -q 'tok_b' "$tmp/o5"

# 6. Arguments and environment reach claude untouched.
assert "args and env forwarded" \
  grep -q 'CLAUDE args=\[-p hi\] url=\[https://banner.test\] toklen=\[5\]' "$tmp/o5b"

# 7. A missing claude gives an actionable error, not a bare failure.
h=$(newhome 7); seed "$h" "https://x.test" "t"; rm -f "$h/bin/claude"
HOME="$h" PATH="/usr/bin:/bin" "$SH" "$JACKAL" -p hi </dev/null >"$tmp/o7" 2>&1 || true
assert "missing claude suggests install" grep -q 'npm i -g @anthropic-ai/claude-code' "$tmp/o7"

printf '\n%d passed, %d failed\n\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
