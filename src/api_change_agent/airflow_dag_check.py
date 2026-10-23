"""Check that a repo's DAGs import and run on the installed Airflow.

    python airflow_dag_check.py [--repo DIR] --junitxml OUT.xml

Standalone on purpose (stdlib + airflow only): the agent runs it with the
python of the venv under test. For every .py file that imports airflow it
does a DagBag import, then `dag.test()` for each DAG the file defines, each
in its own subprocess so one DAG cannot affect another. Results are JUnit
testcases named `<file>::import` and `<file>::dag:<dag_id>`.

Needs a metadata DB; a throwaway SQLite one is created under a temp
AIRFLOW_HOME unless AIRFLOW_HOME is already set.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", "tests", "test"}
IMPORTS_AIRFLOW = re.compile(r"^\s*(import airflow|from airflow[\s.])", re.M)
ERROR_LINE = re.compile(r"(Error|Exception|Traceback|FAILED|failed)")


def dag_files(repo: Path) -> list[Path]:
    found = []
    for path in sorted(repo.rglob("*.py")):
        if SKIP_DIRS & set(path.relative_to(repo).parts):
            continue
        text = path.read_text(errors="replace")
        if IMPORTS_AIRFLOW.search(text) and "DAG" in text:
            found.append(path)
    return found


def env_for(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "AIRFLOW_HOME": str(home),
        "AIRFLOW__CORE__LOAD_EXAMPLES": "False",
        "AIRFLOW__CORE__DAGS_FOLDER": str(home / "dags"),
        "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN": f"sqlite:///{home / 'airflow.db'}",
        "AIRFLOW__CORE__UNIT_TEST_MODE": "False",
        "PYTHONWARNINGS": "ignore",
    })
    return env


def child(file: str, dag_id: str | None) -> None:
    """Runs in a subprocess: import one file, or test one DAG. Prints a JSON line."""
    import warnings

    warnings.simplefilter("ignore")
    from airflow.models.dagbag import DagBag

    import inspect

    kwargs = {"dag_folder": file, "safe_mode": False}
    if "include_examples" in inspect.signature(DagBag.__init__).parameters:
        kwargs["include_examples"] = False  # Airflow 2; 3.x only reads core.load_examples
    bag = DagBag(**kwargs)
    if dag_id is None:
        errors = {Path(k).name: v.strip().splitlines()[-1] for k, v in bag.import_errors.items()}
        print(json.dumps({"ok": not errors, "dags": sorted(bag.dag_ids), "error": "; ".join(errors.values())}))
        return
    dag = bag.dags[dag_id]
    try:
        try:  # Airflow 3: dag.test() needs the DAG serialized via its bundle, like `airflow dags test`
            from airflow.utils.cli import get_bagged_dag
        except ImportError:
            get_bagged_dag = None
        if get_bagged_dag is not None and "bundle_names" in __import__("inspect").signature(get_bagged_dag).parameters:
            dag = get_bagged_dag(None, dag_id, dagfile_path=file)
        run = dag.test()
    except BaseException as e:  # noqa: BLE001 - reported, not raised
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}".splitlines()[0][:300]}))
        return
    state = str(getattr(run, "state", "") or "").lower().rsplit(".", 1)[-1]
    print(json.dumps({"ok": state == "success", "error": "" if state == "success" else f"dag run state: {state}"}))


def python_path(repo: Path, file: Path) -> list[str]:
    """What a deployment puts on sys.path: the dags folder, plugins/ (beside it: AIRFLOW_HOME, or at the
    repo root) and, in most images, the repo root."""
    dags = next((p for p in file.parents if p.name == "dags" and repo in p.parents), file.parent)
    out = [dags, dags.parent / "plugins", repo / "plugins", repo]
    return list(dict.fromkeys(str(p) for p in out if p.is_dir()))


def run_child(args: list[str], env: dict[str, str], timeout: int, repo: Path | None = None) -> tuple[dict, str]:
    # The DAG's own folder is the dags folder (and, on Airflow 3, the bundle root) for this run.
    env = dict(env, AIRFLOW__CORE__DAGS_FOLDER=str(Path(args[0]).parent))
    if repo is not None:
        env["PYTHONPATH"] = os.pathsep.join([*python_path(repo, Path(args[0])), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    try:
        proc = subprocess.run([sys.executable, __file__, "--child", *args], capture_output=True, text=True,
                              env=env, timeout=timeout)
    except subprocess.TimeoutExpired as e:  # one hanging DAG (a sensor, a sleep) must not sink the whole check
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return {"ok": False, "error": f"timed out after {timeout}s"}, out
    output = proc.stdout + proc.stderr
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line), output
            except ValueError:
                break
    return {"ok": False, "error": f"checker crashed (exit {proc.returncode})"}, output


def first_cause(output: str, fallback: str) -> str:
    """The most telling error line from a task log, for the report."""
    lines = [ln.strip() for ln in output.splitlines() if ERROR_LINE.search(ln) and "Traceback" not in ln]
    for ln in reversed(lines):
        if re.search(r"(KeyError|NameError|TypeError|AttributeError|UndefinedError|ImportError|ModuleNotFound)", ln):
            return ln[-300:]
    return (lines[-1][-300:] if lines else fallback) or fallback


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", default=".")
    p.add_argument("--junitxml", required=False)
    p.add_argument("--json", dest="json_out")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--child", nargs="+")
    a = p.parse_args(argv)
    if a.child:
        child(a.child[0], a.child[1] if len(a.child) > 1 else None)
        return 0

    repo = Path(a.repo).resolve()
    home = Path(os.environ["AIRFLOW_HOME"]) if os.environ.get("AIRFLOW_HOME") else Path(tempfile.mkdtemp(prefix="aca-airflow-"))
    (home / "dags").mkdir(parents=True, exist_ok=True)
    env = env_for(home)
    db = subprocess.run([sys.executable, "-m", "airflow", "db", "migrate"], capture_output=True, text=True, env=env)
    if db.returncode:
        print(db.stdout[-2000:] + db.stderr[-2000:], file=sys.stderr)
        return 3

    cases: list[dict] = []
    for f in dag_files(repo):
        rel = f.relative_to(repo).as_posix()
        res, out = run_child([str(f)], env, a.timeout, repo)
        cases.append({"id": f"{rel}::import", "ok": res["ok"], "error": res.get("error") or first_cause(out, "import failed")})
        if not res["ok"]:
            continue
        for dag_id in res["dags"]:
            r, out = run_child([str(f), dag_id], env, a.timeout, repo)
            why = r.get("error", "")
            cases.append({"id": f"{rel}::dag:{dag_id}", "ok": r["ok"],
                          "error": "" if r["ok"] else why if why.startswith("timed out") else first_cause(out, why)})

    for c in cases:
        print(f"{'PASS' if c['ok'] else 'FAIL'} {c['id']}" + ("" if c["ok"] else f"  -- {c['error']}"))
    if a.junitxml:
        suite = ET.Element("testsuite", name="airflow-dags", tests=str(len(cases)),
                           failures=str(sum(not c["ok"] for c in cases)))
        for c in cases:
            path, name = c["id"].split("::", 1)
            tc = ET.SubElement(suite, "testcase", classname=path, name=name)
            if not c["ok"]:
                ET.SubElement(tc, "failure", message=c["error"][:500])
        ET.ElementTree(ET.Element("testsuites")).getroot().append(suite)
        tree = ET.ElementTree(suite)
        tree.write(a.junitxml, encoding="unicode")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(cases, indent=1))
    return 0 if all(c["ok"] for c in cases) else 1


if __name__ == "__main__":
    sys.exit(main())
