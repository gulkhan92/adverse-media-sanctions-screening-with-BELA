#!/usr/bin/env bash
# Start llama.cpp's llama-server with Qwen3-4B-Instruct-2507 (Q4_K_M) on an internal port.
# Binds to loopback only. Fetch the weights first: python scripts/fetch_models.py llm
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MODEL="${LLM_MODEL_PATH:-$ROOT/models/llm/Qwen3-4B-Instruct-2507-Q4_K_M.gguf}"
PORT="${LLM_PORT:-8081}"
exec llama-server -m "$MODEL" --host 127.0.0.1 --port "$PORT" \
  --alias qwen3-4b-instruct-2507 -c 8192 -np 2 -ngl 99 --jinja
