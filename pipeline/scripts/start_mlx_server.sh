#!/usr/bin/env bash
# Start the local MLX OpenAI-compatible server on the host (uses Metal).
# Must run on macOS host, NOT inside Docker. Keep this running in its own terminal.
set -euo pipefail

cd "$(dirname "$0")/.."

# Load .env from repo root if present (for MLX_MODEL / MLX_PORT overrides).
if [ -f ../.env ]; then
  set -a; . ../.env; set +a
fi

MODEL="${MLX_MODEL:-mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit}"
HOST="${MLX_HOST:-127.0.0.1}"
PORT="${MLX_PORT:-8080}"

echo ">> Starting MLX server"
echo "   model: $MODEL"
echo "   addr : http://$HOST:$PORT/v1"
echo "   (first run downloads ~17GB from Hugging Face)"

exec uv run mlx_lm.server \
  --model "$MODEL" \
  --host "$HOST" \
  --port "$PORT" \
  --max-tokens 4096 \
  --decode-concurrency 6 \
  --log-level INFO
