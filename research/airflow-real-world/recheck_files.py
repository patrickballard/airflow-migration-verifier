"""Re-check a few DAG files of a saved repo with the current agent, without rerunning the repo.

    python recheck_files.py owner__name dags/a.py [dags/b.py ...]

Builds a mini repo from the saved original clone (~/SDK/real-world-airflow/_scan/<name>): everything is
kept except the *other* DAG files under dags/ folders, so shared code, plugins and template files still
resolve. Runs the agent on it (dry run, deterministic only: no Claude key in its environment, a throwaway
HOME) with the repo's saved venvs, then the full render comparison of those files. Writes
_results/<name>/recheck.json, which scorecard.py applies over the saved results for those DAGs.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_repo  # noqa: E402

DAG_DEF = re.compile(r"^\s*(from airflow|import airflow)", re.M)


def mini_repo(original: Path, keep: set[str], dest: Path) -> Path:
    shutil.copytree(original, dest, ignore=shutil.ignore_patterns(".git"))
    for f in dest.rglob("*.py"):
        rel = f.relative_to(dest).as_posix()
        text = f.read_text(errors="replace")
        if rel not in keep and "dags" in f.relative_to(dest).parts and DAG_DEF.search(text) \
                and re.search(r"\bDAG\(|@dag\b", text):
            f.unlink()
    return dest


def main():
    name, files = sys.argv[1], sys.argv[2:]
    out_dir = run_repo.RESULTS / name
    af2, af3 = run_repo.VENVS / f"{name}-af2", run_repo.VENVS / f"{name}-af3"
    work = Path(tempfile.mkdtemp(prefix="aca-recheck-"))
    home = work / "home"
    home.mkdir()
    old = mini_repo(run_repo.SCAN / name, set(files), work / "old")
    new = mini_repo(run_repo.SCAN / name, set(files), work / "new")
    env = run_repo.clean_env(None, home)
    env["PYTHONPATH"] = str(run_repo.TOOL / "src")
    t = time.time()
    proc = subprocess.run([str(run_repo.TOOL / ".venv" / "bin" / "python"), "-m", "api_change_agent", "--repo", str(new),
                           "--rules", "airflow", "--dry-run", "--output", str(work / "agent"),
                           "--test-command", f"python {run_repo.DAG_CHECK} --repo . --junitxml {{junit}} "
                                             f"--timeout {run_repo.DAG_TIMEOUT}",
                           "--venv", str(af3), "--render-old-venv", str(af2), "--max-llm-usd", "0"],
                          env=env, capture_output=True, text=True, timeout=1800)
    agent_s = round(time.time() - t)
    result = json.loads((work / "agent" / "result.json").read_text())
    tz = run_repo.find_default_timezone(old)
    render = run_repo.full_render(old, new, af2, af3, files, {run_repo.TZ_ENV: tz.tz} if tz.tz else {})
    before, after = result["tests_before"], result["tests_after"] or result["tests_before"]
    dags = {}
    for i in before["ids"] + after["ids"]:
        if "::dag:" in i and i.split("::")[0] in files:
            f, d = i.split("::dag:", 1)
            key = f"{f}::{d}"
            fields = [x for x in render.get("fields", []) if f"{x['file']}::{x['dag_id']}" == key]
            dags[key] = {
                "before": f"{f}::import" not in before["failures"] and i in before["ids"],
                "after": f"{f}::import" not in after["failures"] and i in after["ids"],
                "fields": len(fields), "mismatched_fields": sum(1 for x in fields if x["n_mismatches"]),
                "unpaired_fields": sum(1 for x in fields if not x["runs"]),
                "rendered": key in render.get("new_dags", []) and key in render.get("old_dags", []),
                "import_error": after["messages"].get(f"{f}::import", ""),
            }
    report = {"files": files, "agent_exit": proc.returncode, "agent_s": agent_s, "dags": dags,
              "unverified": [u for u in result.get("unverified", []) if u["path"] in files],
              "manual": [m for m in result["manual"] if m["path"] in files],
              "rows": [r["rule_id"] for r in result["rows"] if r["path"] in files],
              "llm_usage": result.get("llm_usage"), "render_errors": {**render.get("old_errors", {}),
                                                                       **render.get("new_errors", {})}}
    (out_dir / "recheck.json").write_text(json.dumps(report, indent=1))
    shutil.copy(work / "agent" / "changes.diff", out_dir / "recheck.diff")
    print(json.dumps({k: v for k, v in report.items() if k in ("agent_exit", "agent_s", "dags", "unverified",
                                                                  "render_errors", "llm_usage")}, indent=1))
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
