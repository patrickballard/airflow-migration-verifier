"""Gap category 5 from research/airflow-real-world/findings.md: operator kwargs Airflow 3 renamed that ruff
doesn't rewrite: `task_concurrency=` (the 2.2 alias of `max_active_tis_per_dag=`) and
`ExternalTaskMarker(execution_date=)` (now `logical_date=`).
"""
import os
import shutil
from pathlib import Path

import pytest

from api_change_agent.agent import Options, run
from api_change_agent.plugins import PLUGINS
from api_change_agent.rules import load_rules
from conftest import FIXTURES, ROOT

KWARGS = PLUGINS["airflow_operator_kwargs"]
KW_OPTS = next(r for r in load_rules(ROOT / "rules" / "airflow.yaml").rules if r.id == "airflow3-operator-kwargs").options


# --- 5. renamed kwargs -------------------------------------------------------------------------

@pytest.mark.parametrize("src, expected", [
    ("BashOperator(task_id='t', bash_command='x', task_concurrency=1)\n",
     "BashOperator(task_id='t', bash_command='x', max_active_tis_per_dag=1)\n"),
    ("ExternalTaskMarker(task_id='m', external_dag_id='d', external_task_id='t',\n"
     "                   execution_date='{{ logical_date.replace(hour=2).isoformat() }}')\n",
     "ExternalTaskMarker(task_id='m', external_dag_id='d', external_task_id='t',\n"
     "                   logical_date='{{ logical_date.replace(hour=2).isoformat() }}')\n"),
])
def test_renamed_kwargs(src, expected):
    assert KWARGS.apply(Path("d.py"), src, KW_OPTS) == expected


@pytest.mark.parametrize("src, reason_has", [
    ("BashOperator(task_id='t', task_concurrency=1, max_active_tis_per_dag=2)\n", "both"),
    ("Other(task_id='t', execution_date='x')\n", None),  # only ExternalTaskMarker's execution_date is renamed
])
def test_rename_needs_a_clear_case(src, reason_has):
    assert KWARGS.apply(Path("d.py"), src, KW_OPTS) == src
    sites = KWARGS.find(Path("d.py"), src, KW_OPTS, "llm")
    if reason_has:
        assert sites and reason_has in sites[0].reason
    else:
        assert sites == []


MARKER = '''
import datetime
from airflow import DAG
from airflow.sensors.external_task import ExternalTaskMarker
from airflow.operators.bash_operator import BashOperator

HOURS = "hour=2, minute=0"
with DAG("marker", schedule_interval="@daily", start_date=datetime.datetime(2024, 1, 1), catchup=False) as dag:
    # as in mozilla/telemetry-airflow: the template is built by concatenation
    ExternalTaskMarker(task_id="m", external_dag_id="down", external_task_id="wait",
                       execution_date="{{ execution_date.replace(" + HOURS + ").isoformat() }}")
    BashOperator(task_id="b", bash_command="echo {{ ds }}", task_concurrency=1)
'''
AF2, AF3 = os.environ.get("ACA_AIRFLOW2_VENV"), os.environ.get("ACA_AIRFLOW3_VENV")


@pytest.mark.skipif(not (AF2 and AF3), reason="set ACA_AIRFLOW2_VENV and ACA_AIRFLOW3_VENV")
def test_renamed_kwargs_import_on_airflow_3_and_render_the_same(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "airflow_repo", repo)
    (repo / "dags" / "marker.py").write_text(MARKER.lstrip())
    result = run(Options(repo=repo, rules_path=ROOT / "rules" / "airflow.yaml", dry_run=True, test_command="auto",
                         install_command=None, venv_dir=Path(AF3), render_old_venv=Path(AF2), log=lambda *_: None),
                 model=None)
    text = (repo / "dags" / "marker.py").read_text()
    assert "logical_date=\"{{ logical_date.replace(" in text and "max_active_tis_per_dag=1" in text
    assert "dags/marker.py::import" in result.tests_baseline.failures
    assert "dags/marker.py::import" not in result.tests_after.failures
    (res,) = [r for r in result.render_results if r.dag_id == "marker" and r.field == "logical_date"]
    assert res.runs >= 20 and res.mismatches == []  # Airflow 2's execution_date= vs Airflow 3's logical_date=
