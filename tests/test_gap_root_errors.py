"""Gap category 1 from research/airflow-real-world/findings.md: mechanical fixes.

1. Root errors that kept a file failing, so ruff's unsafe fixes were withheld:
   `from airflow.utils.dates import datetime` (a re-export of the stdlib class) and `provide_context=`
   (a no-op since Airflow 2.0).
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from api_change_agent.agent import Options, run
from api_change_agent.plugins import PLUGINS
from api_change_agent.rules import load_rules
from conftest import FIXTURES, ROOT

RULES = {r.id: r for r in load_rules(ROOT / "rules" / "airflow.yaml").rules}
REEXPORTS, KWARGS = PLUGINS["airflow_reexports"], PLUGINS["airflow_operator_kwargs"]
RE_OPTS, KW_OPTS = RULES["airflow3-reexports"].options, RULES["airflow3-operator-kwargs"].options
AF2, AF3 = os.environ.get("ACA_AIRFLOW2_VENV"), os.environ.get("ACA_AIRFLOW3_VENV")


# --- 1a. stdlib re-exports from airflow.utils.dates -------------------------------------------

@pytest.mark.parametrize("src, expected", [
    ("from airflow.utils.dates import datetime\nx = datetime(2024, 1, 1)\n",
     "from datetime import datetime\nx = datetime(2024, 1, 1)\n"),
    ("from airflow.utils.dates import cron_presets, datetime, timedelta\n",
     "from airflow.utils.dates import cron_presets\nfrom datetime import datetime, timedelta\n"),
    ("from airflow.utils.dates import datetime as dt, relativedelta\n",
     "from datetime import datetime as dt\nfrom dateutil.relativedelta import relativedelta\n"),
])
def test_reexports_point_at_the_real_module(src, expected):
    assert REEXPORTS.find(Path("d.py"), src, RE_OPTS, "llm") == []
    assert REEXPORTS.apply(Path("d.py"), src, RE_OPTS) == expected


def test_other_airflow_utils_dates_names_are_left_alone():
    src = "from airflow.utils.dates import cron_presets, days_ago\n"
    assert REEXPORTS.apply(Path("d.py"), src, RE_OPTS) == src


@pytest.mark.skipif(not AF2, reason="set ACA_AIRFLOW2_VENV")
def test_each_reexport_is_the_same_object_on_airflow_2():
    pairs = [(name, module) for module, names in RE_OPTS["airflow.utils.dates"].items() for name in names]
    script = ("import importlib, warnings; warnings.simplefilter('ignore'); import airflow.utils.dates as d\n"
              f"print(all(getattr(d, n) is getattr(importlib.import_module(m), n) for n, m in {pairs!r}))")
    out = subprocess.run([str(Path(AF2) / "bin" / "python"), "-c", script], capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().endswith("True"), out.stderr[-800:]


# --- 1b. provide_context ---------------------------------------------------------------------

@pytest.mark.parametrize("src, expected", [
    ("PythonOperator(task_id='t', python_callable=f, provide_context=True)\n",
     "PythonOperator(task_id='t', python_callable=f)\n"),
    ("BranchPythonOperator(\n    task_id='b',\n    provide_context=True,\n    python_callable=f,\n)\n",
     "BranchPythonOperator(\n    task_id='b',\n    python_callable=f,\n)\n"),
])
def test_provide_context_is_dropped_from_operator_calls(src, expected):
    assert KWARGS.apply(Path("d.py"), src, KW_OPTS) == expected


def test_calls_that_are_not_operators_are_left_alone():
    src = "configure(provide_context=True, task_concurrency=2)\n"  # no task_id: not an operator
    assert KWARGS.apply(Path("d.py"), src, KW_OPTS) == src and KWARGS.find(Path("d.py"), src, KW_OPTS, "llm") == []


# --- agent-level ---------------------------------------------------------------------------------

BLOCKED = '''
from airflow import DAG
from airflow.operators.python_operator import PythonOperator
from airflow.operators.sql import SQLCheckOperator
from airflow.utils.dates import datetime


def f(**context):
    return 1


with DAG("blocked", start_date=datetime(2024, 1, 1), schedule_interval=None) as dag:
    PythonOperator(task_id="p", python_callable=f, provide_context=True)
    SQLCheckOperator(task_id="c", conn_id="db", sql="select 1")
'''


def agent(tmp_path, venvs=False):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "airflow_repo", repo)
    (repo / "dags" / "blocked.py").write_text(BLOCKED.lstrip())
    kw = dict(test_command="auto", install_command=None, venv_dir=Path(AF3)) if venvs else dict(test_command=None)
    return repo, run(Options(repo=repo, rules_path=ROOT / "rules" / "airflow.yaml", dry_run=True,
                             log=lambda *_: None, **kw), model=None)


def test_ruffs_no_fix_items_for_these_are_gone(tmp_path):
    repo, result = agent(tmp_path)
    text = (repo / "dags" / "blocked.py").read_text()
    assert "provide_context" not in text and "from datetime import datetime" in text
    assert not [m for m in result.manual if m.path == "dags/blocked.py" and "provide_context" in m.reason]


@pytest.mark.skipif(not AF3, reason="set ACA_AIRFLOW3_VENV")
def test_a_file_blocked_twice_now_imports_and_gets_rufffs_fixes(tmp_path):
    repo, result = agent(tmp_path, venvs=True)
    assert "dags/blocked.py" not in {u.path for u in result.unverified}
    assert "dags/blocked.py::import" in result.tests_baseline.failures
    assert "dags/blocked.py::import" not in result.tests_after.failures
    text = (repo / "dags" / "blocked.py").read_text()
    assert "airflow.operators.sql" not in text and "airflow.providers.common.sql" in text  # ruff's move, applied
