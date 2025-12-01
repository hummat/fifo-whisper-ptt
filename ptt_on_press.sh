#!/usr/bin/env bash
set -euo pipefail

FIFO="${FIFO_PATH:-/tmp/dictation_ctl}"

# If the daemon isn't running (no FIFO), silently no-op.
if [[ ! -p "$FIFO" ]]; then
  exit 0
fi

printf 'START
' >"$FIFO"
