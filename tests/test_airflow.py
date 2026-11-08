"""Airflow 2 -> 3 ruleset: ruff layer, our context-key and template rules, the DAG gate.

Unit tests need no Airflow. The integration tests at the bottom run the real
DAG checker and the whole agent against Airflow venvs; point them at venvs
built with the official constraints (e.g. research/target-pick/setup_venvs.sh):

    ACA_AIRFLOW2_VENV=.../airflow-2 ACA_AIRFLOW3_VENV=.../airflow-3 pytest tests/test_airflow.py
"""
import dataclasses
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from api_change_agent import airflow_dag_check, ruff_layer
from api_change_agent.agent import Options, run
from api_change_agent.plugins import PLUGINS
from api_change_agent.report import _dag_table
from api_change_agent.rules import load_rules
from api_change_agent.testrun import TestOutcome, new_failures
from conftest import FIXTURES, ROOT

AIRFLOW_RULES = ROOT / "rules" / "airflow.yaml"
AIRFLOW_REPO = FIXTURES / "airflow_repo"
RULESET = load_rules(AIRFLOW_RULES)
OPTS = next(r for r in RULESET.rules if r.id == "airflow3-context-keys").options
CTX, TPL = PLUGINS["airflow_context_keys"], PLUGINS["airflow_templates"]
CHECK = ROOT / "src" / "api_change_agent" / "airflow_dag_check.py"


def code(s):
    return textwrap.dedent(s).lstrip("\n")


def test_ruleset_loads():
    assert (RULESET.package, RULESET.dist, str(RULESET.target_version)) == ("airflow", "apache-airflow", "3.3.2")
    assert RULESET.verify == "airflow-dags" and "{version}" in RULESET.install_constraints
    (tool,) = RULESET.tools
    assert (tool.tool, tool.select) == ("ruff", "AIR3") and "--unsafe-fixes" in tool.args
    assert [(r.id, r.fix) for r in RULESET.rules] == [
        ("airflow3-days-ago", "deterministic"), ("airflow3-days-ago-judgment", "llm"),
        ("airflow3-reexports", "deterministic"), ("airflow3-operator-kwargs", "deterministic"),
        ("airflow3-operator-kwargs-judgment", "llm"),
        ("airflow3-cron-timetable-pin", "deterministic"), ("airflow3-timetable-judgment", "llm"),
        ("airflow3-removed-dag-methods", "manual"),
        ("airflow3-context-keys", "deterministic"), ("airflow3-templates", "deterministic"),
        ("airflow3-context-judgment", "llm"), ("airflow3-template-judgment", "llm")]


# --- context keys in Python --------------------------------------------------

@pytest.mark.parametrize("src, expected", [
    ('def f(**context):\n    return context["execution_date"]\n',
     'def f(**context):\n    return (context.get("logical_date") or context["dag_run"].run_after)\n'),
    ("def f(**kwargs):\n    return kwargs.get('execution_date', None)\n",
     "def f(**kwargs):\n    return (kwargs.get('logical_date') or kwargs['dag_run'].run_after)\n"),
    ('class Op(BaseOperator):\n    def execute(self, context):\n        return context["execution_date"]\n',
     ('class Op(BaseOperator):\n    def execute(self, context):\n'
      '        return (context.get("logical_date") or context["dag_run"].run_after)\n')),
    ('def f():\n    ctx = get_current_context()\n    return ctx["execution_date"]\n',
     'def f():\n    ctx = get_current_context()\n    return (ctx.get("logical_date") or ctx["dag_run"].run_after)\n'),
    # inside an f-string the replacement keeps the key's quote character (a " in f"..." breaks Python < 3.12)
    ('def f(**context):\n    return f"day {context[\'execution_date\']:%Y-%m-%d}"\n',
     'def f(**context):\n    return f"day {(context.get(\'logical_date\') or context[\'dag_run\'].run_after):%Y-%m-%d}"\n'),
    ('def f(**c):\n    return c["yesterday_ds"]\n',
     'def f(**c):\n    return c["macros"].ds_add(c["ds"], -1)\n'),
    ('def f(**c):\n    return c["tomorrow_ds_nodash"]\n',
     'def f(**c):\n    return c["macros"].ds_format(c["macros"].ds_add(c["ds"], 1), "%Y-%m-%d", "%Y%m%d")\n'),
    ('def f(**c):\n    return c["triggering_dataset_events"]\n',
     'def f(**c):\n    return c["triggering_asset_events"]\n'),
    ('def f(dag_run):\n    return dag_run.execution_date\n',
     'def f(dag_run):\n    return (dag_run.logical_date or dag_run.run_after)\n'),
    # real repo (Workday/dj): a failure email's subject line
    ('def f(dr):\n    return f"{dr.execution_date.strftime(\'%Y\')}"\n',
     'def f(dr):\n    return f"{(dr.logical_date or dr.run_after).strftime(\'%Y\')}"\n'),
])
def test_context_keys_rewritten(src, expected):
    path = Path("dag.py")
    assert CTX.find(path, src, OPTS, "llm") == []
    assert CTX.apply(path, src, OPTS) == expected


class _DagRun:
    def __init__(self, logical_date):
        self.logical_date, self.run_after = logical_date, "fired-at"


@pytest.mark.parametrize("context, expected", [
    ({"logical_date": "logical", "dag_run": _DagRun("logical")}, "logical"),  # scheduled: unchanged value
    ({"dag_run": _DagRun(None)}, "fired-at"),  # Airflow 3 manual run: no logical_date key at all
])
def test_execution_date_rewrite_survives_runs_without_a_logical_date(context, expected):
    # Airflow 3 adds "logical_date" to the context only when the run has one (checked on 3.3.2), so the
    # rewrite must neither raise KeyError nor return None there; Airflow 2 used the trigger time
    for src in ('def f(**context):\n    return context["execution_date"]\n',
                'def f(**context):\n    return context.get("execution_date")\n',
                'def f(**context):\n    dag_run = context["dag_run"]\n    return dag_run.execution_date\n'):
        namespace = {}
        exec(CTX.apply(Path("d.py"), src, OPTS), namespace)  # noqa: S102 - our own rewrite of a test snippet
        assert namespace["f"](**context) == expected


@pytest.mark.parametrize("src, reason_has", [
    ('def helper(ctx):\n    return ctx["execution_date"]\n', "only the code that passes it in"),
    ('def f(**context):\n    return context["next_ds"]\n', "interval timetables"),
    ('def f(**context):\n    return context["prev_execution_date_success"]\n', "may be able to use"),
    ("def f(execution_date, **_):\n    return execution_date\n", "parameter name"),
    ("def f(ti):\n    return ti.execution_date\n", "TaskInstance has no execution_date"),
    ('config = {}\nx = config["execution_date"]\n', "only the code that passes it in"),
])
def test_judgment_sites_left_alone_with_reason(src, reason_has):
    path = Path("dag.py")
    sites = CTX.find(path, src, OPTS, "llm")
    assert sites and reason_has in sites[0].reason
    assert CTX.apply(path, src, OPTS) == src


def test_functions_with_compound_statements():
    # regression: an `if`/`for`/`with` block in a function body crashed the scan (real repo, 2026-10-23)
    src = ('from airflow.operators.python import get_current_context\n'
           'def f():\n    if True:\n        pass\n    for x in []:\n        pass\n'
           '    ctx = get_current_context()\n    return ctx["execution_date"]\n')
    assert CTX.apply(Path("d.py"), src, OPTS) == src.replace(
        'ctx["execution_date"]', '(ctx.get("logical_date") or ctx["dag_run"].run_after)')


def test_unrelated_lookups_untouched():
    src = 'def f(**context):\n    return context["ds"], context["logical_date"], d["other"]\n'
    assert CTX.find(Path("dag.py"), src, OPTS, "deterministic") == [] and CTX.apply(Path("dag.py"), src, OPTS) == src


# --- templates -----------------------------------------------------------------

LD = "(logical_date if logical_date is defined else dag_run.run_after)"  # also under StrictUndefined


@pytest.mark.parametrize("src, expected", [
    ('cmd = "echo {{ execution_date }}"\n', f'cmd = "echo {{{{ {LD} }}}}"\n'),
    ('cmd = "{{ execution_date.strftime(\'%Y\') }} {{ ds }}"\n', f'cmd = "{{{{ {LD}.strftime(\'%Y\') }}}} {{{{ ds }}}}"\n'),
    ('cmd = "{{ execution_date | ts }}"\n', f'cmd = "{{{{ {LD} | ts }}}}"\n'),
    # the replacement's quotes flip to fit inside a "..." literal
    ('cmd = "{{ yesterday_ds_nodash }}"\n',
     "cmd = \"{{ macros.ds_format(macros.ds_add(ds, -1), '%Y-%m-%d', '%Y%m%d') }}\"\n"),
    ("cmd = '{{ yesterday_ds_nodash }}'\n",
     'cmd = \'{{ macros.ds_format(macros.ds_add(ds, -1), "%Y-%m-%d", "%Y%m%d") }}\'\n'),
    ('sql = """\nWHERE d = \'{{ tomorrow_ds }}\'\n"""\n', 'sql = """\nWHERE d = \'{{ macros.ds_add(ds, 1) }}\'\n"""\n'),
    ('cmd = "{% if execution_date %}x{% endif %} {{ dag_run.execution_date }}"\n',
     f'cmd = "{{% if {LD} %}}x{{% endif %}} {{{{ (dag_run.logical_date or dag_run.run_after) }}}}"\n'),
])
def test_templates_in_python_literals(src, expected):
    assert TPL.apply(Path("dag.py"), src, OPTS) == expected


@pytest.mark.parametrize("src", [
    'cmd = "echo execution_date $execution_date"\n',            # not inside {{ }}
    'cmd = "{{ var.value.get(\'execution_date\') }}"\n',          # a string inside the template
    'cmd = f"{{{{ execution_date }}}}"\n',                         # f-strings are skipped
    'cmd = "{{ my_execution_date }} {{ obj.execution_date }}"\n',  # not the bare key / not a known owner
])
def test_templates_left_alone(src):
    assert TPL.apply(Path("dag.py"), src, OPTS) == src


def test_template_files_and_judgment():
    sh = "echo {{ execution_date }} $execution_date\necho {{ next_ds }}\n"
    assert TPL.apply(Path("run.sh"), sh, OPTS) == f"echo {{{{ {LD} }}}} $execution_date\necho {{{{ next_ds }}}}\n"
    (site,) = TPL.find(Path("run.sh"), sh, OPTS, "llm")
    assert site.line == 2 and site.snippet == "next_ds"
    assert TPL.apply(Path("notes.txt"), sh, OPTS) == sh  # not a template file type


# --- ruff layer ------------------------------------------------------------------

def test_ruff_changes_are_credited_and_unfixable_ones_left(tmp_path):
    shutil.copytree(AIRFLOW_REPO, tmp_path / "repo")
    repo = tmp_path / "repo"
    (repo / "dags" / "legacy.py").write_text(code('''
        from airflow import DAG
        from airflow.utils.dates import days_ago

        dag = DAG("legacy", start_date=days_ago(1), schedule_interval=None)
    '''))
    res = ruff_layer.run(repo, ["dags/etl_daily.py", "dags/legacy.py"], RULESET.tools[0])
    by = {(c.path, c.line): c for c in res.changes}
    imports = by[("dags/etl_daily.py", 4)]
    assert set(imports.codes) == {"AIR311", "AIR302"} and "providers.standard.operators.bash" in imports.after
    assert by[("dags/etl_daily.py", 26)].codes == ("AIR301",)
    assert by[("dags/etl_daily.py", 26)].after.strip() == 'schedule="@daily",'
    assert ("dags/legacy.py", "AIR301") in {(d.path, d.code) for d in res.left}  # days_ago: no automatic fix
    assert "days_ago" in " ".join(d.message for d in res.left)


def test_ruff_fixes_drop_the_airflow_imports_they_leave_unused(tmp_path):
    # regression (real repos, 2026-10-23): ruff rewrote DummyOperator -> EmptyOperator but kept
    # `from airflow.operators.dummy import DummyOperator`, which fails to import on Airflow 3
    repo = tmp_path / "repo"
    (repo / "dags").mkdir(parents=True)
    (repo / "dags" / "d.py").write_text(code('''
        import os
        from airflow.models import Variable
        from airflow.operators.dummy import DummyOperator
        from airflow.operators.dummy_operator import DummyOperator as Old

        DummyOperator(task_id="a")
        Old(task_id="b")
    '''))
    res = ruff_layer.run(repo, ["dags/d.py"], RULESET.tools[0])
    text = (repo / "dags" / "d.py").read_text()
    assert "airflow.operators.dummy" not in text and text.count("EmptyOperator(") == 2
    assert "import os\n" in text and "from airflow.models import Variable" in text  # unused before: not ours to remove
    removed = [c for c in res.changes if "airflow.operators.dummy" in c.before]
    assert removed and all(c.codes for c in removed)  # credited to the fix that made them unused


BAD_PYPROJECT = '[project]\nname = "pipeline"\nrequires-python="3.11"\n'  # no operator: not a valid specifier


def test_ruff_runs_isolated_when_the_repo_config_does_not_parse(tmp_path):
    # regression (real repo, 2026-11-08): hubmapconsortium/ingest-pipeline's pyproject.toml has
    # requires-python="3.11"; ruff refused to start on it and the whole run crashed
    shutil.copytree(AIRFLOW_REPO, tmp_path / "repo")
    repo = tmp_path / "repo"
    (repo / "pyproject.toml").write_text(BAD_PYPROJECT)
    notes = []
    diags = ruff_layer.diagnose(repo, ["dags/etl_daily.py"], RULESET.tools[0], notes=notes)
    assert "AIR301" in {d.code for d in diags}
    (note,) = notes
    assert "`pyproject.toml` (line 3): Failed to parse version" in note and "expected operator." in note
    assert "--isolated" in note and "^" not in note
    res = ruff_layer.run(repo, ["dags/etl_daily.py"], RULESET.tools[0])
    assert ("AIR301",) in {c.codes for c in res.changes}


def test_ruff_still_fails_loudly_on_other_errors(tmp_path):
    (tmp_path / "d.py").write_text("x = 1\n")
    broken = dataclasses.replace(RULESET.tools[0], args=("--not-a-ruff-flag",))
    with pytest.raises(RuntimeError, match="ruff failed"):
        ruff_layer.diagnose(tmp_path, ["d.py"], broken)


def test_agent_survives_an_unparsable_repo_config(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(AIRFLOW_REPO, repo)
    (repo / "pyproject.toml").write_text(BAD_PYPROJECT)
    result = run(Options(repo=repo, rules_path=AIRFLOW_RULES, dry_run=True, test_command=None, log=lambda *_: None),
                 model=None)
    assert 'schedule=CronDataIntervalTimetable("@daily"' in (repo / "dags/etl_daily.py").read_text()
    assert any(r.rule_id.startswith("ruff") for r in result.rows)
    assert sum("--isolated" in n for n in result.skipped) == 1
    assert "--isolated" in result.body


# --- gate: progress that uncovers the next failure is not a regression -------------

def outcome(ids, failures):
    return TestOutcome("check", not failures, 1 if failures else 0, frozenset(failures), True, "", len(ids), tuple(ids))


def test_blocked_checks_are_not_regressions():
    before = outcome(["d/a.py::import"], ["d/a.py::import"])
    after = outcome(["d/a.py::import", "d/a.py::dag:a"], ["d/a.py::dag:a"])
    assert new_failures(before, after) == set()
    # a check that existed and passed, now failing, still counts
    before2 = outcome(["d/a.py::import", "d/a.py::dag:a"], [])
    assert new_failures(before2, after) == {"d/a.py::dag:a"}
    # pytest: a module that failed to collect blocks its tests
    b = outcome(["::tests.test_x"], ["::tests.test_x"])
    a = outcome(["tests.test_x::test_one"], ["tests.test_x::test_one"])
    assert new_failures(b, a) == set()


def test_dag_table_shows_not_reached():
    before = outcome(["d/a.py::import"], ["d/a.py::import"])
    after = outcome(["d/a.py::import", "d/a.py::dag:a"], [])
    table = "\n".join(_dag_table(before, after, "apache-airflow 3.3.2"))
    assert "| `d/a.py::dag:a` | ❌ not reached: file failed to import | ✅ |" in table


# --- whole agent, no Airflow needed (tests off) --------------------------------------

def test_agent_on_fixture_without_tests(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(AIRFLOW_REPO, repo)
    result = run(Options(repo=repo, rules_path=AIRFLOW_RULES, dry_run=True, test_command=None), model=None)
    rules = {r.rule_id for r in result.rows}
    assert {"airflow3-context-keys", "airflow3-templates", "apache-airflow 3.3.2"} <= rules
    assert any(r.startswith("ruff AIR3") for r in rules)
    assert (repo / "requirements.txt").read_text() == "apache-airflow==3.3.2\n"
    etl = (repo / "dags" / "etl_daily.py").read_text()
    assert '(context.get("logical_date") or context["dag_run"].run_after)' in etl and "execution_date" not in etl
    assert 'schedule=CronDataIntervalTimetable("@daily", timezone="UTC")' in etl
    assert f"{{{{ {LD}.strftime('%Y-%m-%d') }}}}" in (repo / "dags" / "scripts" / "report.sh").read_text()
    assert "{{ macros.ds_add(ds, 1) }}" in (repo / "dags" / "sql" / "summary.sql").read_text()
    # judgment sites are untouched and listed, each with its reason
    part = (repo / "dags" / "partitions.py").read_text()
    assert 'ctx["execution_date"]' in part and "{{ next_ds }}" in part
    assert sorted((m.path, m.rule_id) for m in result.manual) == [
        ("dags/partitions.py", "airflow3-context-judgment"), ("dags/partitions.py", "airflow3-template-judgment")]
    ruff_rows = [r for r in result.rows if r.rule_id.startswith("ruff ")]
    assert all(r.note.startswith("fixed by ruff AIR3") for r in ruff_rows)
    assert "fixed by ruff AIR301" in result.body and "## Airflow Migration Verifier: Airflow 2 → 3" in result.body


def test_held_airflow_pin_skips_the_upgrade(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(AIRFLOW_REPO, repo)
    (repo / "requirements.txt").write_text("apache-airflow==2.11.2  # pinned: platform team owns upgrades\n")
    result = run(Options(repo=repo, rules_path=AIRFLOW_RULES, dry_run=True, test_command=None), model=None)
    assert (repo / "requirements.txt").read_text().startswith("apache-airflow==2.11.2")
    assert any("marked as intentional" in s for s in result.skipped_rules)
    assert not [r for r in result.rows if r.rule_id.startswith("airflow3-")]  # rules need >=3.0


# --- integration: real Airflow -----------------------------------------------------------

AF2, AF3 = os.environ.get("ACA_AIRFLOW2_VENV"), os.environ.get("ACA_AIRFLOW3_VENV")
needs_af2 = pytest.mark.skipif(not AF2, reason="set ACA_AIRFLOW2_VENV to an Airflow 2 venv")
needs_af3 = pytest.mark.skipif(not AF3, reason="set ACA_AIRFLOW3_VENV to an Airflow 3 venv")


def dag_check(venv, repo):
    env = {k: v for k, v in os.environ.items() if k != "AIRFLOW_HOME"}
    out = subprocess.run([str(Path(venv) / "bin" / "python"), "-I", str(CHECK), "--repo", str(repo)],
                         capture_output=True, text=True, env=env, timeout=1800).stdout
    return {line.split()[1]: line.split()[0] for line in out.splitlines() if line.startswith(("PASS", "FAIL"))}


def deployment_like_repo(root: Path) -> Path:
    """Shared code at the repo root and in plugins/, as Airflow images put them on sys.path; one DAG hangs."""
    (root / "dags").mkdir(parents=True)
    (root / "utils").mkdir()
    (root / "utils" / "__init__.py").write_text("")
    (root / "utils" / "names.py").write_text("OWNER = 'data'\n")
    (root / "plugins" / "operators").mkdir(parents=True)
    (root / "plugins" / "operators" / "__init__.py").write_text("")
    (root / "plugins" / "operators" / "noop.py").write_text("from airflow.operators.empty import EmptyOperator as Noop\n")
    head = "import datetime\nfrom airflow import DAG\n"
    (root / "dags" / "shared.py").write_text(
        head + "from utils.names import OWNER\nfrom operators.noop import Noop\n"
        "with DAG('shared', schedule=None, start_date=datetime.datetime(2024, 1, 1), default_args={'owner': OWNER}):\n"
        "    Noop(task_id='t')\n")
    (root / "dags" / "hangs.py").write_text(
        head + "from airflow.operators.bash import BashOperator\n"
        "with DAG('hangs', schedule=None, start_date=datetime.datetime(2024, 1, 1)):\n"
        "    BashOperator(task_id='t', bash_command='sleep 600')\n")
    return root


def test_python_path_matches_a_deployment(tmp_path):
    repo = deployment_like_repo(tmp_path / "r")
    path = airflow_dag_check.python_path(repo, repo / "dags" / "shared.py")
    assert path[:3] == [str(repo / "dags"), str(repo / "plugins"), str(repo)]
    # AIRFLOW_HOME in a subfolder: airflow/dags + airflow/plugins
    home = repo / "airflow"
    (home / "dags").mkdir(parents=True)
    (home / "plugins").mkdir()
    path = airflow_dag_check.python_path(repo, home / "dags" / "x.py")
    assert path[:2] == [str(home / "dags"), str(home / "plugins")] and str(repo) in path


@needs_af2
def test_checker_finds_shared_code_and_survives_a_hanging_dag(tmp_path):
    repo = deployment_like_repo(tmp_path / "r")
    env = {k: v for k, v in os.environ.items() if k != "AIRFLOW_HOME"}
    out = subprocess.run([str(Path(AF2) / "bin" / "python"), "-I", str(CHECK), "--repo", str(repo), "--timeout", "20"],
                         capture_output=True, text=True, env=env, timeout=600).stdout
    got = {line.split()[1]: line for line in out.splitlines() if line.startswith(("PASS", "FAIL"))}
    assert got["dags/shared.py::import"].startswith("PASS") and got["dags/shared.py::dag:shared"].startswith("PASS")
    assert got["dags/hangs.py::import"].startswith("PASS")
    assert got["dags/hangs.py::dag:hangs"].startswith("FAIL") and "timed out after 20s" in got["dags/hangs.py::dag:hangs"]


@needs_af2
def test_fixture_works_on_airflow_2():
    assert dag_check(AF2, AIRFLOW_REPO) == {
        "dags/etl_daily.py::import": "PASS", "dags/etl_daily.py::dag:etl_daily": "PASS",
        "dags/modern.py::import": "PASS", "dags/modern.py::dag:modern": "PASS",
        "dags/partitions.py::import": "PASS", "dags/partitions.py::dag:partitions": "PASS"}


@needs_af3
def test_fixture_breaks_on_airflow_3():
    assert dag_check(AF3, AIRFLOW_REPO) == {
        "dags/etl_daily.py::import": "FAIL", "dags/modern.py::import": "PASS",
        "dags/modern.py::dag:modern": "PASS", "dags/partitions.py::import": "FAIL"}


@needs_af3
def test_agent_makes_etl_daily_work_on_airflow_3(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(AIRFLOW_REPO, repo)
    result = run(Options(repo=repo, rules_path=AIRFLOW_RULES, dry_run=True, test_command="auto",
                         install_command=None, venv_dir=Path(AF3)), model=None)
    before, after = result.tests_baseline, result.tests_after
    assert "dags/etl_daily.py::import" in before.failures
    assert "dags/etl_daily.py::dag:etl_daily" in after.ids and "dags/etl_daily.py::dag:etl_daily" not in after.failures
    assert "dags/partitions.py::dag:partitions" in after.failures  # needs judgment: reported, not hidden
    assert "KeyError: 'execution_date'" in after.messages["dags/partitions.py::dag:partitions"]
    assert result.new_failures == set()
    assert "| `dags/etl_daily.py::dag:etl_daily` | ❌ not reached: file failed to import | ✅ |" in result.body


# --- the "Manual runs" warning ------------------------------------------------------------

FALLBACK = 'context.get("logical_date") or context["dag_run"].run_after'  # the key is absent, not None


def _manual_runs(body):
    return body.split("### ⚠️ Manual runs", 1)[1].split("\n### ", 1)[0]


def test_execution_date_rewrites_are_guarded_so_there_is_no_manual_runs_warning(tmp_path):
    # regression (real repo, 2026-11-08): `dag_run.execution_date` became plain `dag_run.logical_date`, which
    # is None on an Airflow 3 manual run; a failure email built from it raised instead of sending
    repo = tmp_path / "repo"
    shutil.copytree(AIRFLOW_REPO, repo)
    result = run(Options(repo=repo, rules_path=AIRFLOW_RULES, dry_run=True, test_command=None,
                         repo_slug="acme/dags", branch="api-change-agent/airflow"), model=None)
    assert "Manual runs" not in result.body
    for rel in ("dags/etl_daily.py", "dags/scripts/report.sh", "dags/sql/summary.sql"):
        text = (repo / rel).read_text()
        assert "execution_date" not in text.replace("$execution_date", "") and "run_after" in text, rel


def test_manual_items_point_at_the_migrated_file(tmp_path):
    # regression (2026-11-08): ruff's leftover items were recorded before the timetable pin inserted an
    # import line above them and were never moved, so the PR linked one line too high (to a blank line)
    repo = tmp_path / "repo"
    (repo / "dags").mkdir(parents=True)
    (repo / "requirements.txt").write_text("apache-airflow==2.11.2\n")
    (repo / "dags" / "d.py").write_text(code('''
        import datetime

        from airflow.sdk import (
            DAG,
        )

        with DAG("d", schedule="@daily", start_date=datetime.datetime(2024, 1, 1)) as dag:
            pass

        other = DAG("other", schedule=None, start_date=datetime.datetime.now())
    '''))
    result = run(Options(repo=repo, rules_path=AIRFLOW_RULES, dry_run=True, test_command=None,
                         log=lambda *_: None), model=None)
    final = (repo / "dags" / "d.py").read_text().splitlines()
    assert "from airflow.sdk import CronDataIntervalTimetable" in final  # the insert above the item
    (item,) = [m for m in result.manual if m.rule_id == "ruff AIR304"]
    assert "datetime.datetime.now()" in final[item.line - 1], (item.line, final)


def test_manual_runs_warning_includes_claude_patches(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(AIRFLOW_REPO, repo)

    class JudgmentModel:  # what Claude did on the real run (run 2)
        last_notes = "ctx is the task context; next_ds is the next hourly run"

        def propose(self, request):
            return (request.source.replace('ctx["execution_date"]', 'ctx["logical_date"]')
                    .replace("{{ next_ds }}", "{{ (logical_date + macros.timedelta(hours=1)) | ds }}"))

    result = run(Options(repo=repo, rules_path=AIRFLOW_RULES, dry_run=True, test_command=None), model=JudgmentModel())
    section = _manual_runs(result.body)
    assert "`dags/partitions.py:11`" in section.split("in place of another removed key")[0]  # execution_date -> logical_date
    assert "`dags/partitions.py:20`" in section.split("in place of another removed key")[1]  # next_ds -> logical_date + 1h
    assert section.count("These 1 line(s)") == 2  # only Claude's two unguarded lines, not ours
    assert FALLBACK in section and f"{{{{ {LD} }}}}" in section and "etl_daily.py" not in section


def test_no_manual_runs_section_for_openai(sample_repo):
    from conftest import RULES as OPENAI_RULES
    result = run(Options(repo=sample_repo, rules_path=OPENAI_RULES, dry_run=True, test_command=None), model=None)
    assert "Manual runs" not in result.body


@pytest.mark.parametrize("src, expected", [
    # real repo (mozilla): a template built by concatenation; the kwarg was renamed but the key inside wasn't
    ('d = "{{ execution_date.replace(" + h + ").isoformat() }}"\n',
     f'd = "{{{{ {LD}.replace(" + h + ").isoformat() }}}}"\n'),
    ('d = x + ") }} and {{ execution_date }}"\n', f'd = x + ") }}}} and {{{{ {LD} }}}}"\n'),
    ('d = "a " + ds + " }} {{ yesterday_ds"\n', 'd = "a " + ds + " }} {{ macros.ds_add(ds, -1)"\n'),
])
def test_templates_split_across_concatenated_literals(src, expected):
    assert TPL.apply(Path("dag.py"), src, OPTS) == expected


def test_a_closing_brace_alone_is_not_a_template():
    src = 'msg = "execution_date: }"\n'
    assert TPL.apply(Path("dag.py"), src, OPTS) == src


def test_report_says_why_judgment_rules_didnt_run_and_what_the_pin_is(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(AIRFLOW_REPO, repo)
    body = run(Options(repo=repo, rules_path=AIRFLOW_RULES, dry_run=True, test_command=None), model=None).body
    judgment = [ln for ln in body.splitlines() if ln.startswith("- `airflow3-") and "(llm, not applied)" in ln]
    assert judgment and all("No Anthropic API key was given" in ln for ln in judgment), judgment
    assert "credited in the table above" in body  # there is no per-rule credit below the Rules line
    assert "any sites are listed under" not in body  # don't point at a section that may not exist
    assert "v0" not in body  # the action is v1; say what isn't checked, not which version skips it
    assert "Every row links to the documentation behind it." in body  # ruff rows link to ruff's docs, not a changelog
    # the pin is written `==3.3.2` here; the report mustn't call it `>=3.3.2`
    assert ">=3.3.2" not in body and "`apache-airflow` pins raised to 3.3.2" in body


@needs_af3
def test_execution_date_template_renders_without_a_logical_date():
    # Airflow 3 leaves `logical_date` undefined on a manual run with none; StrictUndefined must not raise
    script = textwrap.dedent(f'''
        from types import SimpleNamespace
        from jinja2 import Environment, StrictUndefined, Undefined
        run = SimpleNamespace(run_after="fired-at")
        for und in (Undefined, StrictUndefined):
            tpl = Environment(undefined=und).from_string("{{{{ {LD} }}}}")
            print(tpl.render(dag_run=run), tpl.render(dag_run=run, logical_date="logical"))
    ''')
    out = subprocess.run([str(Path(AF3) / "bin" / "python"), "-I", "-c", script],
                         capture_output=True, text=True, check=False)
    assert out.stdout.split() == ["fired-at", "logical"] * 2, out.stderr
