#!/usr/bin/env bash
# PROJ-002: PostToolUse hook. Formats and lints the Python file Claude just
# edited. Exit 2 feeds Ruff's output back to Claude so it fixes the errors.
set -euo pipefail

file="$(python3 -c 'import json, sys; print(json.load(sys.stdin).get("tool_input", {}).get("file_path", ""))')"

[[ "$file" == *.py && -f "$file" ]] || exit 0

cd "$CLAUDE_PROJECT_DIR"
uv run --frozen ruff format --quiet "$file"
if ! out="$(uv run --frozen ruff check --fix --quiet "$file" 2>&1)"; then
  echo "$out" >&2
  exit 2
fi
