#!/bin/sh
# Install jackal by symlinking it onto PATH.
# Symlink, not copy: edits to the repo take effect immediately, no drift.
set -eu

bin="${JACKAL_BIN:-$HOME/.local/bin}"
src="$(cd "$(dirname "$0")" && pwd)/jackal"

[ -f "$src" ] || { echo "install: $src not found" >&2; exit 1; }

mkdir -p "$bin"
chmod +x "$src"
ln -sf "$src" "$bin/jackal"

echo "installed  $bin/jackal -> $src"

case ":$PATH:" in
  *":$bin:"*) ;;
  *) echo "warning: $bin is not on your PATH — add it in your shell rc" >&2 ;;
esac

echo "run 'jackal' to configure, or 'jackal --setup' to change an existing config"
