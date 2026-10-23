"""Operator classes ruff swaps in must accept the call's keyword arguments on Airflow 3.

Found on a real repo (AMPATH/etl): ruff rewrote MySqlOperator(..., mysql_conn_id=...) to
SQLExecuteQueryOperator(..., mysql_conn_id=...), which Airflow 3 rejects when the DAG is parsed.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from api_change_agent import kwarg_check
from api_change_agent.agent import Options, run
from conftest import FIXTURES, ROOT

AF3 = os.environ.get("ACA_AIRFLOW3_VENV")
needs_af3 = pytest.mark.skipif(not AF3, reason="set ACA_AIRFLOW3_VENV")
SQL = "airflow.providers.common.sql.operators.sql.SQLExecuteQueryOperator"

ORIGINAL = '''from airflow.operators.mysql_operator import MySqlOperator

t = MySqlOperator(
    task_id="t", sql="select 1", mysql_conn_id="db", autocommit=True,
)
u = MySqlOperator(task_id="u", sql="x", **extra)
'''
FINAL = '''from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator

t = SQLExecuteQueryOperator(
    task_id="t", sql="select 1", mysql_conn_id="db", autocommit=True,
)
u = SQLExecuteQueryOperator(task_id="u", sql="x", **extra)
'''


def test_finds_the_classes_ruff_brought_in_and_what_they_replaced():
    found = kwarg_check.introduced_classes(ORIGINAL, FINAL)
    assert found == {"SQLExecuteQueryOperator": (SQL, "MySqlOperator")}


def test_flags_kwargs_the_new_class_does_not_accept():
    signatures = {SQL: {"accepts": ["sql", "autocommit", "conn_id", "task_id", "database"], "open": False}}
    (p,) = kwarg_check.problems(FINAL, kwarg_check.introduced_classes(ORIGINAL, FINAL), signatures)
    assert (p.line, p.cls, p.kwarg, p.replaced) == (4, "SQLExecuteQueryOperator", "mysql_conn_id", "MySqlOperator")
    assert "conn_id=" in p.reason and "Airflow 3" in p.reason and "MySqlOperator" in p.reason


def test_open_signatures_and_unknown_classes_are_not_guessed():
    found = kwarg_check.introduced_classes(ORIGINAL, FINAL)
    assert kwarg_check.problems(FINAL, found, {SQL: {"accepts": [], "open": True}}) == []   # takes **kwargs
    assert kwarg_check.problems(FINAL, found, {SQL: {"error": "ModuleNotFoundError"}}) == []  # can't import


@needs_af3
def test_signatures_come_from_airflow_3():
    out = subprocess.run([str(Path(AF3) / "bin" / "python"), "-I", str(kwarg_check.SCRIPT), SQL,
                          "airflow.providers.standard.operators.bash.BashOperator", "no.such.Operator"],
                         capture_output=True, text=True, timeout=300)
    sigs = json.loads(out.stdout.strip().splitlines()[-1])
    assert {"conn_id", "sql", "task_id"} <= set(sigs[SQL]["accepts"]) and not sigs[SQL]["open"]
    assert "mysql_conn_id" not in sigs[SQL]["accepts"]
    assert "bash_command" in sigs["airflow.providers.standard.operators.bash.BashOperator"]["accepts"]
    assert "error" in sigs["no.such.Operator"]


@needs_af3
def test_agent_lists_the_rejected_kwarg_as_manual(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "airflow_repo", repo)
    (repo / "dags" / "mysql_dag.py").write_text(
        "import datetime\nfrom airflow import DAG\nfrom airflow.operators.mysql_operator import MySqlOperator\n\n"
        "with DAG('mysql_dag', schedule_interval=None, start_date=datetime.datetime(2024, 1, 1)):\n"
        "    MySqlOperator(task_id='t', sql='select 1', mysql_conn_id='db')\n")
    result = run(Options(repo=repo, rules_path=ROOT / "rules" / "airflow.yaml", dry_run=True, test_command="auto",
                         install_command=None, venv_dir=Path(AF3), log=lambda *_: None), model=None)
    text = (repo / "dags" / "mysql_dag.py").read_text()
    assert "SQLExecuteQueryOperator(" in text                       # ruff's swap is still shown...
    (m,) = [m for m in result.manual if m.rule_id == "kwarg-check"]  # ...but the kwarg it breaks is listed
    assert m.path == "dags/mysql_dag.py" and "mysql_conn_id" in m.reason and "SQLExecuteQueryOperator" in m.reason
    assert "mysql_conn_id" in text.splitlines()[m.line - 1]
    row = next(r for r in result.rows if r.path == "dags/mysql_dag.py" and "SQLExecuteQueryOperator(" in r.after)
    assert "mysql_conn_id" in row.note


@needs_af3
def test_classes_it_cannot_read_are_named_not_skipped_silently(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "airflow_repo", repo)
    (repo / "dags" / "http_dag.py").write_text(
        "import datetime\nfrom airflow import DAG\nfrom airflow.operators.http_operator import SimpleHttpOperator\n\n"
        "with DAG('http_dag', schedule_interval=None, start_date=datetime.datetime(2024, 1, 1)):\n"
        "    SimpleHttpOperator(task_id='t', endpoint='x', http_conn_id='api')\n")
    result = run(Options(repo=repo, rules_path=ROOT / "rules" / "airflow.yaml", dry_run=True, test_command="auto",
                         install_command=None, venv_dir=Path(AF3), log=lambda *_: None), model=None)
    note = next((n for n in result.skipped if n.startswith("kwarg check couldn't read")), "")
    assert "HttpOperator" in note  # the http provider isn't in the test venv
