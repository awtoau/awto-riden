#!/usr/bin/env bash
set -euo pipefail

# One-command regeneration for timing artifacts used in docs and README.

PORT="${1:-/dev/ttyUSB0}"
VOLTAGE="${2:-12}"
CURRENT="${3:-1.5}"

source .venv/bin/activate
python3 scripts/timing_test_set.py \
  --port "$PORT" \
  --voltage "$VOLTAGE" \
  --current "$CURRENT" \
  --mode both \
  --quick-samples 12 \
  --comprehensive-samples 80 \
  --quick-poll-ms 0,100,150 \
  --comprehensive-poll-ms 0,20,50,100,150

echo "Done: regenerated timing suite artifacts in docs/."
