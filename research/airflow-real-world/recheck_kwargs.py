"""Run the agent's kwarg check on a repo's saved trees (original clone vs migrated copy). No rerun.

    python recheck_kwargs.py owner__name
"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from api_change_agent import kwarg_check  # noqa: E402

BASE = Path.home() / "SDK" / "real-world-airflow"
name = sys.argv[1]
original, migrated = BASE / "_scan" / name, BASE / "_migrated" / name
python = BASE / ".venvs" / f"{name}-af3" / "bin" / "python"
found = {}
for f in sorted(migrated.rglob("*.py")):
    rel = f.relative_to(migrated)
    old = original / rel
    if not old.exists() or old.read_text(errors="replace") == f.read_text(errors="replace"):
        continue
    try:
        intro = kwarg_check.introduced_classes(old.read_text(errors="replace"), f.read_text(errors="replace"))
    except Exception:  # noqa: BLE001
        continue
    if intro:
        found[rel] = intro
sigs = kwarg_check.signatures(python, sorted({p for i in found.values() for p, _ in i.values()}))
total = Counter()
for rel, intro in found.items():
    for p in kwarg_check.problems((migrated / rel).read_text(), intro, sigs):
        total[(p.cls, p.kwarg, p.replaced)] += 1
        print(f"{rel}:{p.line}  {p.reason}")
print(f"\n{sum(total.values())} problem(s) in {len(found)} file(s) with swapped/moved classes:", dict(total))
print("could not import:", sorted(k for k, v in sigs.items() if "error" in v))
