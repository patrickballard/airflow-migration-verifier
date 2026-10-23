"""Run every repo in repos.txt through run_repo.py, sharing one Claude budget.

    python run_all.py [--budget 2.0] [--only owner/name ...]

Each repo gets min(PER_REPO, what's left) of the budget; spend is read back from the
agent's result.json. After every repo, scorecard.py regenerates scorecard_data.json, so
partial results are always on disk.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = Path.home() / "SDK" / "real-world-airflow" / "_results"
PER_REPO = 0.32  # second pass: $3.25 left of the $5 limit, split evenly


def spent(name: str) -> float:
    p = RESULTS / name / "agent" / "result.json"
    if not p.exists():
        return 0.0
    return float((json.loads(p.read_text()).get("llm_usage") or {}).get("usd") or 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=2.0)
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    repos = [ln.split()[0] for ln in (HERE / "repos.txt").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    todo = a.only or repos
    used = sum(spent(r.replace("/", "__")) for r in repos if r not in todo)
    for repo in todo:
        name = repo.replace("/", "__")
        cap = round(max(0.0, min(PER_REPO, a.budget - used)), 2)
        t = time.time()
        r = subprocess.run([sys.executable, str(HERE / "run_repo.py"), repo, "--max-llm-usd", str(cap)],
                           capture_output=True, text=True)
        wall = round(time.time() - t)
        timing = RESULTS / name / "timing.json"
        data = json.loads(timing.read_text()) if timing.exists() else {}
        data.update(total_s=wall, llm_cap_usd=cap, harness_exit=r.returncode)
        if r.returncode:
            data["harness_error"] = r.stderr[-3000:]
        timing.parent.mkdir(parents=True, exist_ok=True)
        timing.write_text(json.dumps(data, indent=1))
        used += spent(name)
        print(f"{repo}: exit={r.returncode} {wall}s spent=${spent(name):.3f} total=${used:.3f}", flush=True)
        subprocess.run([sys.executable, str(HERE / "scorecard.py")], capture_output=True)


if __name__ == "__main__":
    main()
