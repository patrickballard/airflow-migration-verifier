#!/usr/bin/env bash
# Run the agent locally in dry-run mode on one clone. No push, no PR.
set -u
name="$1"; base="$HOME/SDK/real-world"; out="$base/_results/$name"
mkdir -p "$out"
# start from the committed simulation, never from a previous dry-run's edits
git -C "$base/$name" checkout -q -- . && git -C "$base/$name" clean -qfd
start=$(date +%s)
env -u ANTHROPIC_API_KEY -u GITHUB_ACTIONS -u GITHUB_TOKEN \
  PYTHONPATH="$HOME/SDK/api-change-agent/src" \
  "$HOME/SDK/api-change-agent/.venv/bin/python" -m api_change_agent \
    --repo "$base/$name" --dry-run --output "$out" \
    --test-command auto --install-command auto --venv "$base/.venvs/$name" \
    ${VENV_PYTHON:+--venv-python "$VENV_PYTHON"} \
    > "$out/log.txt" 2>&1
echo "$name exit=$? $(( $(date +%s) - start ))s"
