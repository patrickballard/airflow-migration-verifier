#!/bin/bash
# usage: scan.sh owner__name ...   verify mode on a clean copy of each clone's HEAD; nothing is pushed anywhere
BASE=$HOME/SDK/real-world-airflow
TOOL=$HOME/SDK/api-change-agent
for name in "$@"; do
  out=$BASE/_verify/$name; tree=$BASE/_verify_trees/$name
  rm -rf "$out" "$tree"; mkdir -p "$out" "$tree"
  git -C "$BASE/_scan/$name" archive HEAD | tar -x -C "$tree"
  git -C "$BASE/_scan/$name" rev-parse HEAD > "$out/commit"
  s=$(date +%s)
  (cd "$tree" && HOME=$(mktemp -d) "$TOOL/.venv/bin/python" -m api_change_agent verify --before . --after . \
     --venv "$BASE/.venvs/$name-af3" --old-venv "$BASE/.venvs/$name-af2" --output "$out" > "$out/stdout.log" 2>&1)
  echo "$name exit=$? $(( $(date +%s)-s ))s"
done
