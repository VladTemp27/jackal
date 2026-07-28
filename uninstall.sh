#!/bin/sh
# Remove the jackal symlink. Pass --purge to also delete the credential file.
set -eu

bin="${JACKAL_BIN:-$HOME/.local/bin}"
cfg="$HOME/.jackal.env"
purge=0
case "${1:-}" in --purge) purge=1 ;; esac

if [ -e "$bin/jackal" ] || [ -L "$bin/jackal" ]; then
  rm -f "$bin/jackal"
  echo "removed  $bin/jackal"
else
  echo "nothing at $bin/jackal"
fi

# ponytail: config survives by default — an uninstall that silently deletes
# credentials is the kind of thing you only notice afterwards.
if [ "$purge" = 1 ]; then
  rm -f "$cfg"
  echo "removed  $cfg"
elif [ -e "$cfg" ]; then
  echo "kept     $cfg (holds your token) — delete with: $0 --purge"
fi
