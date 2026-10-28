#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ "$#" -lt 1 ]; then
  echo "Usage: ./run_orchestrator_v0.21.sh PROJECT.pdw [POLL_SECONDS]"
  exit 2
fi
POLL_SECONDS="${2:-60}"
python3 workbench_headless_v0.21.py --project "$1" --daemon --poll-seconds "$POLL_SECONDS"
