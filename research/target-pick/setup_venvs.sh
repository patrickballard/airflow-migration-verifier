#!/usr/bin/env bash
# One venv per (library, version) under ./.venvs, Python 3.12 (numpy 1.26 has no 3.13 wheels).
set -uo pipefail
cd "$(dirname "$0")"
PY=$(uv python find 3.12)
mk() {  # name, then uv pip install args
  local name=$1; shift
  [ -x ".venvs/$name/bin/python" ] || uv venv -q -p "$PY" ".venvs/$name"
  uv pip install -q -p ".venvs/$name/bin/python" "$@" >".venvs/$name.log" 2>&1 && echo "ok   $name" || echo "FAIL $name (see .venvs/$name.log)"
}
mk pydantic-1   "pydantic==1.10.26" &
mk pydantic-2   "pydantic==2.14.1" pydantic-settings &
mk sqlalchemy-1 "sqlalchemy==1.4.54" &
mk sqlalchemy-2 "sqlalchemy==2.1.6" &
mk numpy-1      "numpy==1.26.4" &
mk numpy-2      "numpy==2.5.4" &
mk pandas-2     "pandas==2.3.3" &
mk pandas-3     "pandas==3.1.0" &
wait
mk langchain-0  "langchain==0.3.30" "langchain-community<0.4" &
mk langchain-1  "langchain==1.4.7" &
mk llamaindex-0 "llama-index==0.9.48" &
mk llamaindex-1 "llama-index==0.14.25" &
mk marshmallow-3 "marshmallow==3.26.2" &
mk marshmallow-4 "marshmallow==4.3.1" &
wait
mk airflow-2    "apache-airflow==2.11.2" -c constraints/airflow-2.11.2-py3.12.txt &
mk airflow-3    "apache-airflow==3.3.2" -c constraints/airflow-3.3.2-py3.12.txt &
mk tools        "bump-pydantic" "ruff" "langchain-cli" "llama-index-cli" &
wait
