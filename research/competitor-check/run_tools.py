"""Run each migration tool on the 10 saved ORIGINAL repos, then verify every output the same way.

    python run_tools.py transform [tool ...]     # make ~/SDK/competitor-check/trees/<tool>/<repo>
    python run_tools.py verify [tool ...]        # api-change-agent verify each tree against the original
    python run_tools.py table                    # results.json + the per-tool numbers

Originals: ~/SDK/real-world-airflow/_scan/<repo> (clones at the commits in research/airflow-real-world).
Venvs: the scorecard's per-repo Airflow 2.11.2 / 3.3.2 venvs (providers included). Each tool runs with its
documented usage, latest version, a throwaway HOME and AIRFLOW_HOME, no credentials, no hand edits.
Other people's code stays out of this repo; only commands, logs and numbers are committed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL_REPO = HERE.parents[1]
WORK = Path.home() / "SDK" / "competitor-check"
SCAN = Path.home() / "SDK" / "real-world-airflow" / "_scan"
VENVS = Path.home() / "SDK" / "real-world-airflow" / ".venvs"
REPOS = [ln.split()[0].replace("/", "__") for ln in (TOOL_REPO / "research/airflow-real-world/repos.txt").read_text().splitlines()
         if ln.strip() and not ln.startswith("#")]
TV = WORK / "venvs"
DAG_CHECK = TOOL_REPO / "src" / "api_change_agent" / "airflow_dag_check.py"

# tool -> argv run inside the copied repo ({x} filled below). Documented usage, quoted in REPORT.md.
TOOLS = {
    # ruff alone, as the Astronomer skill and Airflow's upgrade guide document it
    "ruff": ["{tv}/ruff/bin/ruff", "check", "--preview", "--select", "AIR", "--fix", "--unsafe-fixes", "--no-cache",
             "--exit-zero", "."],
    # README: `airflow-v2-to-v3 migrate /path/to/dags/` (in place)
    "v2v3": ["{tv}/v2v3/bin/airflow-v2-to-v3", "migrate", "."],
    # README: `migration-helper fix ./dags/ --safe-only`; --unsafe also run, so it isn't judged on its safest mode
    "mhelper": ["{tv}/mhelper/bin/migration-helper", "fix", ".", "--safe-only", "--report-dir", "{out}/mhelper-report"],
    "mhelper-unsafe": ["{tv}/mhelper/bin/migration-helper", "fix", ".", "--unsafe", "--report-dir",
                       "{out}/mhelper-report"],
    # us: deterministic only ($0), the DAG check import-only, the render check on
    "ours": [str(TOOL_REPO / ".venv/bin/python"), "-m", "api_change_agent", "--repo", ".", "--rules", "airflow",
             "--dry-run", "--output", "{out}/agent", "--test-command",
             f"python {DAG_CHECK} --repo . --junitxml {{{{junit}}}} --import-only --timeout 45",  # {junit} after .format
             "--venv", "{af3}", "--render-old-venv", "{af2}", "--max-llm-usd", "0"],
}


def clean_env(home: Path, ours: bool = False) -> dict:
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TMPDIR", "TERM") if k in os.environ}
    env.update(HOME=str(home), AIRFLOW_HOME=str(home / "airflow"), PYTHONWARNINGS="ignore")
    if ours:  # only our runs see our package
        env["PYTHONPATH"] = str(TOOL_REPO / "src")
    return env


def fresh_copy(repo: str, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(SCAN / repo, dest, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    return dest


def transform(tool: str, repo: str) -> dict:
    tree = fresh_copy(repo, WORK / "trees" / tool / repo)
    out = WORK / "logs" / tool / repo
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    home = Path(tempfile.mkdtemp(prefix="aca-cc-home-"))
    argv = [a.format(tv=TV, out=out, af3=VENVS / f"{repo}-af3", af2=VENVS / f"{repo}-af2") for a in TOOLS[tool]]
    t = time.time()
    p = subprocess.run(argv, cwd=tree, env=clean_env(home, ours=tool == "ours"), capture_output=True, text=True,
                       timeout=7200)
    rec = {"tool": tool, "repo": repo, "argv": argv, "cwd": "<copy of the original repo>", "exit": p.returncode,
           "seconds": round(time.time() - t)}
    (out / "stdout.txt").write_text(p.stdout[-400_000:])
    (out / "stderr.txt").write_text(p.stderr[-100_000:])
    (out / "run.json").write_text(json.dumps(rec, indent=1))
    shutil.rmtree(home, ignore_errors=True)
    return rec


def verify(tool: str, repo: str) -> dict:
    tree = WORK / "trees" / tool / repo
    out = WORK / "verify" / tool / repo
    if out.exists():
        shutil.rmtree(out)
    home = Path(tempfile.mkdtemp(prefix="aca-cc-home-"))
    argv = [str(TOOL_REPO / ".venv/bin/python"), "-m", "api_change_agent", "verify", "--before", str(SCAN / repo),
            "--after", str(tree), "--venv", str(VENVS / f"{repo}-af3"), "--old-venv", str(VENVS / f"{repo}-af2"),
            "--output", str(out)]
    t = time.time()
    p = subprocess.run(argv, env=clean_env(home, ours=True), capture_output=True, text=True, timeout=7200)
    out.mkdir(parents=True, exist_ok=True)
    (out / "stderr.txt").write_text(p.stderr[-100_000:])
    shutil.rmtree(home, ignore_errors=True)
    return {"tool": tool, "repo": repo, "exit": p.returncode, "seconds": round(time.time() - t)}


def universe() -> dict[str, set[str]]:
    """The scorecard's DAGs: load on Airflow 2.11.2, fail to import on Airflow 3.3.2 as written."""
    recs = json.loads((TOOL_REPO / "research/airflow-real-world/scorecard_data.json").read_text())
    return {r["repo"].replace("/", "__"): {k for k, d in r["dags"].items() if not d["before"]} for r in recs}


def table(tools: list[str]) -> dict:
    out, scope = {}, universe()
    for tool in tools:
        for repo in sorted(scope):
            f = WORK / "verify" / tool / repo / "result.json"
            if not f.exists():
                continue
            res = json.loads(f.read_text())
            dags = res["dags"]
            removed = {m["path"] for m in res["manual"] if m["rule_id"] == "airflow3-removed-dag-methods"}
            row = out.setdefault(tool, {"repos": 0, "dags": 0, "imports": 0, "identical": 0, "no_fields": 0,
                                        "mismatch": 0, "not_render_checked": 0, "not_rendered": 0, "headline": 0,
                                        "removed_method_dags": 0, "kwarg_problems": 0, "unverified_files": 0,
                                        "unpinned_schedules": 0, "examples": []})
            row["repos"] += 1
            row["kwarg_problems"] += sum(1 for m in res["manual"] if m["rule_id"] == "kwarg-check")
            row["unverified_files"] += len(res.get("unverified", []))
            row["unpinned_schedules"] += len(res.get("unpinned", []))
            for key in scope[repo]:
                d = dags.get(key) or {"imports": False, "render": "not reached"}
                row["dags"] += 1
                row["imports"] += d["imports"]
                if not d["imports"]:
                    continue
                kind = d["render"].replace(" ", "_").replace("-", "_")
                kind = {"no_template_fields": "no_fields"}.get(kind, kind)
                if kind in row:
                    row[kind] += 1
                rm = key.split("::")[0] in removed
                row["removed_method_dags"] += rm
                row["headline"] += d["render"] in ("identical", "no template fields") and not rm
                if d["render"] == "mismatch" and len(row["examples"]) < 3:
                    row["examples"].append(f"{repo.split('__')[0]} {key}: {d['example'][:300]}")
    return out


def main():
    cmd, tools = sys.argv[1], sys.argv[2:] or list(TOOLS)
    if cmd == "table":
        t = table(tools if sys.argv[2:] else [*TOOLS, "astro-skill"])
        (HERE / "results.json").write_text(json.dumps(t, indent=1))
        for tool, r in t.items():
            print(f"{tool:15} repos={r['repos']} dags={r['dags']} imports={r['imports']} identical={r['identical']} "
                  f"no_fields={r['no_fields']} mismatch={r['mismatch']} unchecked={r['not_render_checked']} "
                  f"headline={r['headline']} unverified_files={r['unverified_files']} unpinned={r['unpinned_schedules']}")
        return
    repos = [r for r in REPOS if not os.environ.get("ONLY") or r in os.environ["ONLY"].split(",")]
    for tool in tools:
        for repo in repos:
            if cmd == "transform" and tool in TOOLS:
                rec = transform(tool, repo)
            elif cmd == "verify":
                if not (WORK / "trees" / tool / repo).exists():
                    continue
                rec = verify(tool, repo)
            else:
                continue
            print(json.dumps(rec)[:300], flush=True)


if __name__ == "__main__":
    main()
