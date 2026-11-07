#!/usr/bin/env bash
# 一键启动后端（:8000）和前端（:5173），Ctrl+C 一起退出。
set -euo pipefail
cd "$(dirname "$0")/.."
uv run uvicorn backend.app.main:app --port 8000 --reload --reload-dir backend &
BACKEND=$!
trap 'kill $BACKEND 2>/dev/null' EXIT
npm --prefix frontend run dev
