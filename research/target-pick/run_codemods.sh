#!/usr/bin/env bash
# Run each library's official/popular codemod on its sample, then run the result on the NEW version.
cd "$(dirname "$0")"
T=.venvs/tools/bin
out=results/codemods; rm -rf "$out"; mkdir -p "$out"
try() {  # lib new-venv tool-command...
  local lib=$1 venv=$2; shift 2
  mkdir -p "$out/$lib" && cp "codemod-samples/$lib/sample.py" "$out/$lib/sample.py"
  (cd "$out/$lib" && "$@" > tool.log 2>&1); echo "exit=$?" >> "$out/$lib/tool.log"
  diff -u "codemod-samples/$lib/sample.py" "$out/$lib/sample.py" > "$out/$lib/changes.diff"
  AIRFLOW_HOME="$PWD/.venvs/airflow-home-3" ".venvs/$venv/bin/python" -I -W ignore "$out/$lib/sample.py" > "$out/$lib/run_new.txt" 2>&1
  echo "== $lib: $(grep -c '^[-+][^-+]' "$out/$lib/changes.diff") changed lines; on new version: $(tail -1 "$out/$lib/run_new.txt" | cut -c1-150)"
}
try pydantic   pydantic-2  "$PWD/$T/bump-pydantic" .
try numpy      numpy-2     "$PWD/$T/ruff" check --select NPY201 --fix --unsafe-fixes --no-cache sample.py
try airflow    airflow-3   "$PWD/$T/ruff" check --select AIR --preview --fix --unsafe-fixes --no-cache sample.py
try langchain  langchain-1 "$PWD/$T/langchain-cli" migrate
# llama-index-cli is deprecated and crashes on current core; use the 0.10-era CLI it was built with
try llamaindex llamaindex-1 "$PWD/.venvs/li-cli-0.10/bin/llamaindex-cli" upgrade-file sample.py
