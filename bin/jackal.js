#!/usr/bin/env node
// Resolve the interpreter here instead of letting npm's generated Windows shim
// do it. That shim copies the interpreter *name* out of the shebang and does a
// bare PATH lookup for `python3` — and on Windows that name is claimed by the
// Microsoft Store app-execution-alias stub, which shadows a python.org install,
// prints "Python was not found", and exits without running anything. Copying
// python.exe to python3.exe cannot fix that: it adds a second python3.exe
// further down PATH, and the stub still resolves first. See issue #3.

'use strict'

const { spawnSync } = require('child_process')
const path = require('path')

const SCRIPT = path.join(__dirname, '..', 'jackal')

// python3 first (correct everywhere it exists), then python, then the Windows
// `py` launcher — which is what python.org installs and is the one name the
// Store alias never shadows.
const CANDIDATES = [['python3'], ['python'], ['py', '-3']]

// Single quotes only: Node re-quotes arguments for CreateProcess on Windows,
// and a `-c` body free of double quotes survives that round trip unmangled.
const OK = 'jackal-py-ok:'
const PROBE = `import sys; print('${OK}' + sys.executable if sys.version_info >= (3, 9) else '')`

// ponytail: a candidate that resolves to a .bat/.cmd shim (pyenv-win) cannot be
// spawned without `shell: true`, so it fails the probe and we fall through to
// `py -3`. If someone reports that, probe with a shell on Windows — safe there
// because the probe takes no user input, unlike the run below.

// The alias stub announces itself, on stdout in some builds and stderr in
// others, so match the text across both. Its exit code is 9009 — cmd.exe's
// generic "not recognized" code, which is suggestive but not unique to it.
const STUB_TEXT = /Microsoft Store|Python was not found/i
const STUB_EXIT = 9009

let sawStub = false

// A candidate counts only if it echoes the sentinel back. Checking the exit
// code alone would trust anything that exits 0, including a stub that never
// ran Python; the sentinel proves an interpreter actually executed the probe.
// Python 2 parses PROBE fine and prints an empty line, so it fails the same way.
function findPython() {
  for (const cmd of CANDIDATES) {
    const r = spawnSync(cmd[0], [...cmd.slice(1), '-c', PROBE], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    const line = (r.stdout || '').split('\n').find((l) => l.startsWith(OK))
    if (line) {
      // Prefer the absolute sys.executable the interpreter reported over the
      // name we looked up: it is always a real binary, so the run below needs
      // no second PATH lookup and cannot land on a different Python than the
      // one we just vetted.
      const exe = line.slice(OK.length).trim()
      return exe ? [exe] : cmd
    }
    if (r.status === STUB_EXIT || STUB_TEXT.test(`${r.stdout}${r.stderr}`)) sawStub = true
  }
  return null
}

const python = findPython()

if (!python) {
  const tried = CANDIDATES.map((c) => c.join(' ')).join(', ')
  process.stderr.write(
    `jackal: no usable Python 3.9+ found (tried: ${tried})\n\n` +
      (sawStub
        ? '  `python3` on your PATH is the Microsoft Store app-execution-alias\n' +
          '  stub, not an interpreter. Turn it off under Settings > Apps >\n' +
          '  Advanced app settings > App execution aliases, or install Python\n' +
          '  from the Store.\n'
        : '  Install Python 3.9 or newer: https://www.python.org/downloads/\n')
  )
  process.exit(1)
}

const run = spawnSync(python[0], [...python.slice(1), SCRIPT, ...process.argv.slice(2)], {
  stdio: 'inherit',
})

if (run.error) {
  process.stderr.write(`jackal: could not run ${python.join(' ')}: ${run.error.message}\n`)
  process.exit(1)
}

// Re-raise rather than inventing an exit code, so the shell reports a killed
// jackal the same way it would report a killed interpreter.
if (run.signal) process.kill(process.pid, run.signal)
process.exit(run.status === null ? 1 : run.status)
