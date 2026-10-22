#!/usr/bin/env bash
# One venv per SDK version under ./.venvs (ignored by git).
set -euo pipefail
cd "$(dirname "$0")"
mk() { python3 -m venv ".venvs/$1" && ".venvs/$1/bin/pip" install -q "${@:2}"; }
mk v1.109.1 "openai==1.109.1" respx pytest pytest-httpx vcrpy &   # last 1.x
mk v2.54.0  "openai==2.54.0" &                                     # last 2.x (to date removals)
mk v3.36.1  "openai==3.36.1" &                                     # latest, clean
mk v3x      "openai==3.36.1" httpx respx pytest pytest-httpx vcrpy & # latest + httpx declared
wait
