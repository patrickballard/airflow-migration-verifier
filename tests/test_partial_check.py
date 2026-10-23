"""After a Claude patch, the Airflow DAG check re-tests only the DAG files the patch touched.

Every other file's results are reused from the last full check. A file that other DAG files
import, or one the checker doesn't cover, still gets the full check.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from api_change_agent import testrun
from api_change_agent.agent import Options, run
from api_change_agent.testrun import TestOutcome, merge_outcome
from conftest import FIXTURES, ROOT

AIRFLOW_RULES = ROOT / "rules" / "airflow.yaml"
AIRFLOW_REPO = FIXTURES / "airflow_repo"
CHECK = ROOT / "src" / "api_change_agent" / "airflow_dag_check.py"
AF3 = os.environ.get("ACA_AIRFLOW3_VENV")


def outcome(ids, failures, messages=None):
    return TestOutcome("check", not failures, 1 if failures else 0, frozenset(failures), True, "", len(ids),
                       tuple(ids), messages or {})


def test_merge_replaces_only_the_retested_files():
    base = outcome(["a.py::import", "a.py::dag:a", "b.py::import"], ["a.py::dag:a", "b.py::import"],
                   {"a.py::dag:a": "boom", "b.py::import": "nope"})
    partial = outcome(["b.py::import", "b.py::dag:b"], ["b.py::dag:b"], {"b.py::dag:b": "later"})
    merged = merge_outcome(base, partial, ["b.py"])
    assert merged.ids == ("a.py::import", "a.py::dag:a", "b.py::import", "b.py::dag:b")
    assert merged.failures == {"a.py::dag:a", "b.py::dag:b"}
    assert merged.messages == {"a.py::dag:a": "boom", "b.py::dag:b": "later"}
    assert merged.executed == 4 and not merged.passed and merged.has_junit
    clean = merge_outcome(outcome(["a.py::import"], []), outcome(["b.py::import"], []), ["b.py"])
    assert clean.passed and clean.returncode == 0


class Recorder:
    """Stands in for the DAG checker: every file imports; records which files each run checked."""

    def __init__(self, monkeypatch):
        self.runs: list[tuple[str, ...] | None] = []
        monkeypatch.setattr(testrun.TestRunner, "setup", lambda self: None)
        rec = self

        def fake_run(runner, extra_args=()):
            files = tuple(extra_args[1:]) if extra_args[:1] == ("--files",) else None
            rec.runs.append(files)
            names = files or ("dags/etl_daily.py", "dags/modern.py", "dags/partitions.py")
            ids = [i for f in names for i in (f"{f}::import", f"{f}::dag:{Path(f).stem}")]
            return outcome(ids, [])

        monkeypatch.setattr(testrun.TestRunner, "run", fake_run)


class Patch:
    last_notes = "replayed"

    def propose(self, request):
        return (request.source.replace('ctx["execution_date"]', 'ctx["logical_date"]')
                .replace("{{ next_ds }}", "{{ data_interval_end | ds }}"))


def agent(tmp_path, extra=None):
    repo = tmp_path / "repo"
    shutil.copytree(AIRFLOW_REPO, repo)
    for rel, text in (extra or {}).items():
        (repo / rel).write_text(text)
    return repo, run(Options(repo=repo, rules_path=AIRFLOW_RULES, dry_run=True, test_command="auto",
                             install_command=None, log=lambda *_: None), model=Patch())


def test_claude_patches_retest_only_their_file(tmp_path, monkeypatch):
    rec = Recorder(monkeypatch)
    repo, result = agent(tmp_path)
    assert any(r.rule_id.endswith("judgment") for r in result.rows)  # Claude's patches were applied
    full, partial = [r for r in rec.runs if r is None], [r for r in rec.runs if r is not None]
    assert partial and all(files == ("dags/partitions.py",) for files in partial)
    assert len(full) == 2  # the baseline and the deterministic stage; no full re-check per patch
    assert set(result.tests_after.ids) >= {"dags/etl_daily.py::import", "dags/partitions.py::dag:partitions"}


def test_a_file_other_dags_import_gets_the_full_check(tmp_path, monkeypatch):
    rec = Recorder(monkeypatch)
    helper = "from partitions import _partition_path  # shares the helper\n"
    agent(tmp_path, {"dags/modern.py": helper + (AIRFLOW_REPO / "dags" / "modern.py").read_text()})
    assert all(r is None for r in rec.runs)  # partitions.py is imported elsewhere: never checked alone


@pytest.mark.skipif(not AF3, reason="set ACA_AIRFLOW3_VENV")
def test_checker_files_option_checks_only_those_files():
    out = subprocess.run([str(Path(AF3) / "bin" / "python"), "-I", str(CHECK), "--repo", str(AIRFLOW_REPO),
                          "--files", "dags/modern.py"], capture_output=True, text=True, timeout=900).stdout
    ids = [line.split()[1] for line in out.splitlines() if line.startswith(("PASS", "FAIL"))]
    assert ids and all(i.startswith("dags/modern.py::") for i in ids)
