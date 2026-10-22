#!/usr/bin/env bash
# Re-run every proof and diff; writes results/*.txt. Needs ./setup_venvs.sh first.
set -euo pipefail
cd "$(dirname "$0")"
V=.venvs
for v in v1.109.1 v2.54.0 v3.36.1; do
  "$V/$v/bin/python" -I surface.py > "results/surface-${v#v}.json"
  "$V/$v/bin/python" -I fields.py  > "results/fields-${v#v}.json"
done
python3 diff_surface.py results 1.109.1 2.54.0 3.36.1 | tee results/surface_diff.txt
"$V/v1.109.1/bin/python" run.py v1.109.1="$V/v1.109.1/bin/python" v3.36.1="$V/v3.36.1/bin/python" \
  v3+httpx="$V/v3x/bin/python" | tee results/matrix.txt
for v in v1.109.1 v3x; do
  echo "== mocking libraries under $v"
  "$V/$v/bin/python" -I -m pytest -q -p no:cacheprovider -W ignore pt 2>&1 | tail -3
done | tee results/mocking_libs.txt
