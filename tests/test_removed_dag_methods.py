"""DAG methods Airflow 3 removed (e.g. following_schedule): flagged as manual, never patched.

They import fine and only fail when called, so the DAG check's import passes while the DAG
breaks at run time (found on a real repo: research/airflow-real-world/findings.md).
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from api_change_agent.agent import Options, run
from api_change_agent.plugins import PLUGINS
from api_change_agent.rules import load_rules
from conftest import FIXTURES, ROOT

AIRFLOW_RULES = ROOT / "rules" / "airflow.yaml"
RULE = next(r for r in load_rules(AIRFLOW_RULES).rules if r.id == "airflow3-removed-dag-methods")
CHECK = PLUGINS["airflow_removed_dag_methods"]
AF2, AF3 = os.environ.get("ACA_AIRFLOW2_VENV"), os.environ.get("ACA_AIRFLOW3_VENV")

LISTING = """
import json, datetime, warnings
warnings.simplefilter("ignore")
try:
    from airflow.sdk import DAG
except ImportError:
    from airflow.models.dag import DAG
d = DAG("x", schedule="@daily", start_date=datetime.datetime(2024, 1, 1))
names = []
for n in dir(d):
    if n.startswith("_") or isinstance(getattr(type(d), n, None), property):
        continue  # properties may need the metadata DB
    try:
        if callable(getattr(d, n)):
            names.append(n)
    except Exception:
        pass
print(json.dumps(sorted(names)))
"""


@pytest.mark.skipif(not (AF2 and AF3), reason="set ACA_AIRFLOW2_VENV and ACA_AIRFLOW3_VENV")
def test_the_list_is_exactly_what_airflow_3_dropped(tmp_path):
    def methods(venv):
        (tmp_path / Path(venv).name).mkdir()
        out = subprocess.run([str(Path(venv) / "bin" / "python"), "-c", LISTING], capture_output=True, text=True,
                             env=dict(os.environ, AIRFLOW_HOME=str(tmp_path / Path(venv).name)), timeout=300)
        assert out.returncode == 0, out.stderr[-1500:]
        return set(json.loads(out.stdout.strip().splitlines()[-1]))
    assert sorted(methods(AF2) - methods(AF3)) == sorted(RULE.options["methods"])


@pytest.mark.parametrize("src, method", [
    ("def f(dag):\n    return dag.following_schedule(dt)\n", "following_schedule"),
    ("def f(**context):\n    return context['dag'].previous_schedule(d)\n", "previous_schedule"),
    ("def f(**kwargs):\n    return kwargs.get('dag').get_run_dates(a, b)\n", "get_run_dates"),
    ("class Op:\n    def execute(self, context):\n        self.dag.create_dagrun(run_id='x')\n", "create_dagrun"),
    ("from airflow import DAG\nwith DAG('d') as my_dag:\n    pass\nmy_dag.clear()\n", "clear"),
    ("x = DAG('d')\nx.get_dagrun(logical_date=d)\n", "get_dagrun"),
    ("def f(ti):\n    return ti.task.dag.get_num_active_runs()\n", "get_num_active_runs"),
    ("def f(obj):\n    return obj.following_schedule(d)\n", "following_schedule"),  # distinctive: any receiver
    ("nxt = dag.following_schedule\n", "following_schedule"),                     # a reference, not a call
])
def test_flags_removed_methods(src, method):
    (site,) = CHECK.find(Path("d.py"), src, RULE.options, "llm")
    assert method in site.reason and "removed in Airflow 3" in site.reason
    assert CHECK.apply(Path("d.py"), src, RULE.options) == src  # never rewritten


@pytest.mark.parametrize("src", [
    "import pandas as pd\npd.date_range('2024-01-01', periods=3)\n",   # not a DAG
    "def f(job):\n    job.run()\n    job.clear()\n",
    "def f(dag):\n    dag.timetable.next_dagrun_info(None, None)\n",   # the timetable's, which still exists
    "def f(dag):\n    dag.test()\n    dag.get_task('t')\n",              # methods Airflow 3 kept
])
def test_leaves_other_calls_alone(src):
    assert CHECK.find(Path("d.py"), src, RULE.options, "llm") == []


def test_following_schedule_hint_names_the_replacement():
    (site,) = CHECK.find(Path("d.py"), "def f(dag):\n    return dag.following_schedule(dt)\n", RULE.options, "llm")
    assert "data_interval_end" in site.reason


def test_agent_lists_them_as_manual_and_never_asks_claude(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "airflow_repo", repo)
    (repo / "dags" / "cond.py").write_text(
        "import datetime\nfrom airflow import DAG\nfrom airflow.operators.python_operator import PythonOperator\n\n"
        "def _check(execution_date, dag, **_):\n    return dag.following_schedule(execution_date)\n\n"
        "with DAG('cond', schedule_interval='@daily', start_date=datetime.datetime(2024, 1, 1)) as dag:\n"
        "    PythonOperator(task_id='t', python_callable=_check)\n")

    class NoCalls:
        last_notes = ""

        def propose(self, request):
            assert request.rule.id != "airflow3-removed-dag-methods", "manual checks are never sent to Claude"
            return request.source

    result = run(Options(repo=repo, rules_path=AIRFLOW_RULES, dry_run=True, test_command=None, log=lambda *_: None),
                 model=NoCalls())
    (m,) = [m for m in result.manual if m.rule_id == "airflow3-removed-dag-methods"]
    final = (repo / "dags" / "cond.py").read_text().splitlines()
    assert m.path == "dags/cond.py" and "dag.following_schedule" in final[m.line - 1]  # a line of the PR's file
    assert "following_schedule" in m.reason
    assert "airflow3-removed-dag-methods" in result.body
