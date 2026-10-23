"""Run the Airflow 2 -> 3 agent on one public repo, locally. No push, no PR.

    python run_repo.py owner/name [--steps venvs,baseline,agent,render] [--max-llm-usd 0.4]

Clones live in ~/SDK/real-world-airflow/_scan/<owner__name> (from find_candidates.py) and are
reset to their commit before the agent runs. Per repo it:

1. venvs: builds Airflow 2.11.2 and 3.3.2 venvs (uv, Python 3.12, official constraints) with the
   repo's declared requirements and every provider its DAGs import; falls back to fewer extras
   when the full set doesn't resolve.
2. baseline: DagBag import + dag.test() of every DAG on Airflow 2.11.2, original code.
3. agent: the agent's dry run with the render check (old venv = the Airflow 2 one). Their code
   runs with HOME set to a temp dir and no credentials in the environment; the Claude key is
   handed to the agent process only (the agent strips it from what repo code sees).
4. render: an independent full render comparison: every template field of every DAG, original
   code on Airflow 2 vs migrated code on Airflow 3, paired by fire time (the headline number).

Everything lands in ~/SDK/real-world-airflow/_results/<owner__name>/.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

TOOL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOL / "src"))
from api_change_agent import render_check  # noqa: E402
from api_change_agent.airflow_config import ENV as TZ_ENV  # noqa: E402
from api_change_agent.airflow_config import find_default_timezone  # noqa: E402
from api_change_agent.testrun import _junit_failures  # noqa: E402

BASE = Path.home() / "SDK" / "real-world-airflow"
SCAN, VENVS, RESULTS = BASE / "_scan", BASE / ".venvs", BASE / "_results"
DAG_CHECK = TOOL / "src" / "api_change_agent" / "airflow_dag_check.py"
KEY_FILE = Path.home() / ".config" / "api-change-agent" / "anthropic_key"
PY = "3.12"
AF2, AF3 = "2.11.2", "3.3.2"
DAG_TIMEOUT = 45  # seconds per dag.test(); sensors would otherwise poke for days
TWO_LEVEL = {"apache", "cncf", "microsoft", "common", "dbt", "alibaba", "atlassian", "fab"}
DEV = re.compile(r"^(pytest|black|flake8|isort|mypy|ruff|pre-commit|pylint|coverage|tox|ipython|jupyter|notebook|"
                 r"apache-airflow|sqlfluff|pytest-.*|types-.*)", re.I)
MODULE_TO_PIP = {"yaml": "pyyaml", "sklearn": "scikit-learn", "bs4": "beautifulsoup4", "dotenv": "python-dotenv",
                 "PIL": "pillow", "psycopg2": "psycopg2-binary", "cv2": "opencv-python", "jwt": "pyjwt",
                 "great_expectations_provider": "airflow-provider-great-expectations", "dateutil": "python-dateutil",
                 "googleapiclient": "google-api-python-client", "telegram": "python-telegram-bot",
                 "snowflake": "snowflake-connector-python", "MySQLdb": "mysqlclient", "kafka": "kafka-python",
                 "docker": "docker", "slack_sdk": "slack-sdk", "airflow_dbt": "airflow-dbt", "cosmos": "astronomer-cosmos"}
REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._\-]*(?:\[[^\]]*\])?)")


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def clean_env(venv: Path | None, home: Path) -> dict:
    """Only what a build needs: no tokens, no cloud credentials, a throwaway HOME."""
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "TMPDIR", "TERM") if k in os.environ}
    if venv:
        env["PATH"] = f"{venv / 'bin'}:{env.get('PATH', '')}"
        env["VIRTUAL_ENV"] = str(venv)
    env.update(HOME=str(home), PYTHONWARNINGS="ignore", NO_PROXY="*")
    return env


# --- step 1: venvs ---------------------------------------------------------------------------

def declared(repo: Path) -> list[str]:
    names = []
    for p in repo.rglob("*"):
        if not p.is_file() or ".git" in p.parts or p.stat().st_size > 200_000:
            continue
        lines = []
        if re.match(r"requirements.*\.txt$", p.name):
            lines = p.read_text(errors="replace").splitlines()
        elif p.name.startswith(("Dockerfile", "docker-compose", "compose")):
            text = p.read_text(errors="replace")
            for m in re.finditer(r"pip3? install ([^\n&|;]+)", text):
                lines += m.group(1).split()
            for m in re.finditer(r"_PIP_ADDITIONAL_REQUIREMENTS:\s*\$\{_PIP_ADDITIONAL_REQUIREMENTS:-([^}]*)\}", text):
                lines += m.group(1).split()
        for ln in lines:
            ln = ln.split("#")[0].strip()
            if not ln or ln.startswith(("-", "git+", "http", ".", "/", "$")):
                continue
            if m := REQ_NAME.match(ln):
                names.append(m.group(1))
    return sorted({n for n in names if not DEV.match(n) or n.lower().startswith("apache-airflow-providers-")})


def imported_providers(repo: Path) -> list[str]:
    found = set()
    for p in repo.rglob("*.py"):
        if ".git" in p.parts:
            continue
        for m in re.finditer(r"airflow\.providers\.(\w+)(?:\.(\w+))?", p.read_text(errors="replace")):
            a, b = m.group(1), m.group(2)
            found.add(f"apache-airflow-providers-{a}-{b}" if a in TWO_LEVEL and b else f"apache-airflow-providers-{a}")
    return sorted(n.replace("_", "-") for n in found)


def constraints(version: str) -> Path:
    for py in (PY, "3.11", "3.10", "3.9", "3.8"):  # old releases have no 3.12 file; provider pins match across them
        path = BASE / f"constraints-{version}-{py}.txt"
        if path.exists():
            return path
        try:
            url = f"https://raw.githubusercontent.com/apache/airflow/constraints-{version}/constraints-{py}.txt"
            path.write_bytes(urllib.request.urlopen(url, timeout=60).read())
            return path
        except OSError:
            continue
    raise RuntimeError(f"no constraints file for Airflow {version}")


# latest patch of each 2.x minor, by release month: a repo's era when nothing pins it
RELEASES = [("2020-12", "2.0.2"), ("2021-05", "2.1.4"), ("2021-10", "2.2.5"), ("2022-04", "2.3.4"), ("2022-09", "2.4.3"),
            ("2022-12", "2.5.3"), ("2023-04", "2.6.3"), ("2023-08", "2.7.3"), ("2023-12", "2.8.4"), ("2024-04", "2.9.3"),
            ("2024-08", "2.10.5"), ("2025-05", "2.11.0")]


def era(repo: Path) -> tuple[str, str]:
    """(Airflow 2 version the repo was written against, how we know)."""
    for p in sorted(repo.rglob("*")):
        if not p.is_file() or ".git" in p.parts or p.stat().st_size > 300_000:
            continue
        if not (p.name.startswith(("requirements", "constraints", "Dockerfile", "docker-compose", "compose", "pyproject",
                                   "setup", ".env")) or p.suffix in (".txt", ".toml")):
            continue
        text = p.read_text(errors="replace")
        if m := re.search(r"apache[-_]airflow(?:\[[^\]]*\])?\s*==\s*(2\.\d+\.\d+)", text):
            return m.group(1), f"pin in {p.relative_to(repo)}"
        if m := re.search(r"apache/airflow:(?:\$\{[^}:]*:-)?(2\.\d+\.\d+)", text):
            return m.group(1), f"image in {p.relative_to(repo)}"
    date = sh(["git", "-C", str(repo), "log", "-1", "--format=%cs"]).stdout.strip()[:7]
    version = next((v for d, v in reversed(RELEASES) if d <= date), RELEASES[0][1])
    return version, f"last commit {date}"


def provider_pins(path: Path) -> dict[str, str]:
    return {m.group(1).lower(): m.group(2) for m in
            re.finditer(r"^(apache-airflow-providers-[\w-]+)==([\w.]+)", path.read_text(), re.M)}


def merged_constraints(version: str, era_version: str, declared_pins: dict[str, str]) -> Path:
    """The target's constraints, with provider versions from the repo's era (and its own pins) where it has them."""
    pins = {**provider_pins(constraints(era_version)), **declared_pins}
    out = BASE / f"constraints-{version}-providers-{era_version}-{abs(hash(tuple(sorted(declared_pins.items()))))}.txt"
    lines = [ln for ln in constraints(version).read_text().splitlines()
             if not re.match(r"apache-airflow-providers-([\w-]+)==", ln) or ln.split("==")[0].lower() not in pins]
    out.write_text("\n".join(lines + [f"{k}=={v}" for k, v in sorted(pins.items())]) + "\n")
    return out


def has_package(repo: Path) -> bool:
    py = repo / "pyproject.toml"
    return (repo / "setup.py").exists() or (py.exists() and re.search(r"^\[(project|tool\.poetry)\]", py.read_text(), re.M))


def build_venv(path: Path, version: str, providers: list[str], extras: list[str], home: Path,
               repo: Path, era_version: str | None = None, declared_pins: dict | None = None) -> dict:
    if (path / "bin" / "python").exists():
        shutil.rmtree(path)
    sh(["uv", "venv", "-q", "--python", PY, str(path)], check=True)
    core = [f"apache-airflow=={version}"] + (["apache-airflow-providers-standard"] if version.startswith("3") else [])
    prov = [p for p in providers if not (version.startswith("2") and p.endswith("-standard"))]
    base = ["uv", "pip", "install", "-q", "--python", str(path / "bin" / "python")]
    env = {**os.environ, "HOME": os.environ["HOME"]}  # uv needs its cache; nothing of theirs runs here
    plans = []
    if era_version:  # Airflow 2: providers as the repo knew them
        era_c = merged_constraints(version, era_version, declared_pins or {})
        plans += [("all, era providers", era_c, core + prov + extras), ("providers only, era", era_c, core + prov)]
    plans += [("all", constraints(version), core + prov + extras), ("providers only", constraints(version), core + prov)]
    info = None
    for attempt, c, pkgs in plans:
        if sh(base + ["-c", str(c)] + pkgs, env=env).returncode == 0:
            info = {"version": version, "installed": attempt, "providers": prov,
                    "extras": extras if attempt.startswith("all") else []}
            break
    if info is None:
        pip = base + ["-c", str(constraints(version))]
        ok, failed = [], []
        sh(pip + core, env=env, check=True)
        for p in prov + extras:  # one by one: keep what installs
            (ok if sh(pip + core + [p], env=env).returncode == 0 else failed).append(p)
        info = {"version": version, "installed": "one by one", "ok": ok, "failed": failed}
    if has_package(repo):  # the repo's own package (DAGs import it); its deps if they resolve
        pip = base + ["-c", str(constraints(version))]
        info["own_package"] = ("with deps" if sh(pip + ["-e", str(repo)], env=env).returncode == 0 else
                               "no deps" if sh(pip + ["--no-deps", "-e", str(repo)], env=env).returncode == 0 else "failed")
    return info


# --- step 2/4: DAG check and full render -----------------------------------------------------

def dag_check(venv: Path, repo: Path, home: Path) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "j.xml"
        t = time.time()
        r = sh([str(venv / "bin" / "python"), str(DAG_CHECK), "--repo", ".", "--junitxml", str(junit),
                "--timeout", str(DAG_TIMEOUT)], cwd=repo, env=clean_env(venv, home), timeout=7200)
        if not junit.exists():
            return {"error": (r.stdout + r.stderr)[-2000:], "seconds": round(time.time() - t)}
        failures, _, ids, messages = _junit_failures(str(junit))
        return {"ids": list(ids), "failures": sorted(failures), "messages": messages, "seconds": round(time.time() - t)}


def full_render(repo_old: Path, repo_new: Path, af2: Path, af3: Path, files: list[str], tz_env: dict) -> dict:
    os.environ.pop("ANTHROPIC_API_KEY", None)
    old = render_check.render(af2 / "bin" / "python", repo_old, files, env=tz_env)
    new = render_check.render(af3 / "bin" / "python", repo_new, files, env=tz_env)
    results = render_check.compare(old, new, changed=set(), whole_dags=set(new["dags"]))
    return {"old_dags": sorted(old["dags"]), "new_dags": sorted(new["dags"]), "old_errors": old["errors"],
            "new_errors": new["errors"],
            "fields": [{"file": r.file, "dag_id": r.dag_id, "task_id": r.task_id, "field": r.field, "runs": r.runs,
                        "mismatches": [m.__dict__ for m in r.mismatches[:3]], "n_mismatches": len(r.mismatches),
                        "summary": render_check.summary(r)} for r in results]}


# --- main ------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--steps", default="venvs,baseline,agent,render")
    ap.add_argument("--max-llm-usd", default="0.4")
    a = ap.parse_args()
    name = a.repo.replace("/", "__")
    repo, out = SCAN / name, RESULTS / name
    out.mkdir(parents=True, exist_ok=True)
    af2, af3 = VENVS / f"{name}-af2", VENVS / f"{name}-af3"
    home = Path(tempfile.mkdtemp(prefix="aca-home-"))
    timing = json.loads((out / "timing.json").read_text()) if (out / "timing.json").exists() else {}
    steps = a.steps.split(",")

    def reset():
        sh(["git", "-C", str(repo), "checkout", "-q", "--", "."])
        sh(["git", "-C", str(repo), "clean", "-qfdx"])

    reset()
    if "venvs" in steps:
        t = time.time()
        deps = {"declared": declared(repo), "imported_providers": imported_providers(repo)}
        providers = sorted({*deps["imported_providers"], *(d for d in deps["declared"]
                                                          if d.lower().startswith("apache-airflow-providers-"))})
        extras = [d for d in deps["declared"] if not d.lower().startswith("apache-airflow-providers-")]
        deps["era"] = era(repo)
        declared_pins = {}
        for f in repo.rglob("requirements*.txt"):
            if ".git" not in f.parts:
                declared_pins.update(provider_pins(f))
        deps["af2"] = build_venv(af2, AF2, providers, extras, home, repo, deps["era"][0], declared_pins)
        deps["af3"] = build_venv(af3, AF3, providers, extras, home, repo)
        (out / "deps.json").write_text(json.dumps(deps, indent=1))
        timing["venvs_s"] = round(time.time() - t)
    if "baseline" in steps:
        res = dag_check(af2, repo, home)
        missing = sorted({m.group(1).split(".")[0] for msg in res.get("messages", {}).values()
                          for m in [re.search(r"No module named '([\w.]+)'", msg)] if m} - {"airflow"})
        local = {p.stem for p in repo.rglob("*.py")} | {p.name for p in repo.rglob("*") if p.is_dir()}
        missing = [MODULE_TO_PIP.get(m, m.replace("_", "-")) for m in missing if m not in local]
        if missing:  # one round: install what the DAGs import but nothing declares, then check again
            deps = json.loads((out / "deps.json").read_text())
            deps["added_for_imports"] = {}
            for venv, version in ((af2, AF2), (af3, AF3)):
                pip = ["uv", "pip", "install", "-q", "--python", str(venv / "bin" / "python"),
                       "-c", str(constraints(version))]
                deps["added_for_imports"][version] = [m for m in missing if sh(pip + [m]).returncode == 0]
            (out / "deps.json").write_text(json.dumps(deps, indent=1))
            res = dag_check(af2, repo, home)
        (out / "af2_baseline.json").write_text(json.dumps(res, indent=1))
        timing["af2_baseline_s"] = res.get("seconds")
        reset()
    if "agent" in steps:
        env = clean_env(None, home)
        env["PYTHONPATH"] = str(TOOL / "src")
        if KEY_FILE.exists():  # to the agent only; it strips the key from what repo code sees
            env["ANTHROPIC_API_KEY"] = KEY_FILE.read_text().strip()
        cmd = [str(TOOL / ".venv" / "bin" / "python"), "-m", "api_change_agent", "--repo", str(repo),
               "--rules", "airflow", "--dry-run", "--output", str(out / "agent"),
               "--test-command", f"python {DAG_CHECK} --repo . --junitxml {{junit}} --timeout {DAG_TIMEOUT}",
               "--venv", str(af3), "--render-old-venv", str(af2), "--max-llm-usd", a.max_llm_usd]
        t = time.time()
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=4 * 3600)
        env.pop("ANTHROPIC_API_KEY", None)
        timing["agent_s"] = round(time.time() - t)
        (out / "agent").mkdir(exist_ok=True)
        (out / "agent" / "log.txt").write_text(r.stdout[-200_000:] + "\n--- stderr ---\n" + r.stderr[-50_000:])
        timing["agent_exit"] = r.returncode
        # keep the migrated tree for the full render (step 4); the clone is reset afterwards
        migrated = BASE / "_migrated" / name
        if migrated.exists():
            shutil.rmtree(migrated)
        shutil.copytree(repo, migrated, ignore=shutil.ignore_patterns(".git"))
        reset()
    if "render" in steps:
        migrated = BASE / "_migrated" / name
        result = json.loads((out / "agent" / "result.json").read_text())
        before = (result.get("tests_before") or {}).get("ids") or []
        files = sorted({i.split("::")[0] for i in before if i.endswith("::import")})
        tz = find_default_timezone(repo)
        tz_env = {TZ_ENV: tz.tz} if tz.tz else {}
        t = time.time()
        try:
            res = full_render(repo, migrated, af2, af3, files, tz_env)
        except Exception as e:  # noqa: BLE001
            res = {"error": str(e)[-2000:]}
        res["timezone"] = {"tz": tz.tz, "where": tz.where(), "problem": tz.problem}
        timing["full_render_s"] = round(time.time() - t)
        (out / "full_render.json").write_text(json.dumps(res, indent=1))
    (out / "timing.json").write_text(json.dumps(timing, indent=1))
    shutil.rmtree(home, ignore_errors=True)
    print(name, json.dumps(timing))


if __name__ == "__main__":
    main()
