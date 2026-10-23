"""Airflow 2 -> 3 ruleset: ruff layer, our context-key and template rules, the DAG gate.

Unit tests need no Airflow. The integration tests at the bottom run the real
DAG checker and the whole agent against Airflow venvs; point them at venvs
built with the official constraints (e.g. research/target-pick/setup_venvs.sh):

    ACA_AIRFLOW2_VENV=.../airflow-2 ACA_AIRFLOW3_VENV=.../airflow-3 pytest tests/test_airflow.py
"""
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from api_change_agent import ruff_layer
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
        ("airflow3-cron-timetable-pin", "deterministic"), ("airflow3-timetable-judgment", "llm"),
        ("airflow3-context-keys", "deterministic"), ("airflow3-templates", "deterministic"),
        ("airflow3-context-judgment", "llm"), ("airflow3-template-judgment", "llm")]


# --- context keys in Python --------------------------------------------------

@pytest.mark.parametrize("src, expected", [
    ('def f(**context):\n    return context["execution_date"]\n',
     'def f(**context):\n    return context["logical_date"]\n'),
    ("def f(**kwargs):\n    return kwargs.get('execution_date', None)\n",
     "def f(**kwargs):\n    return kwargs.get('logical_date', None)\n"),
    ('class Op(BaseOperator):\n    def execute(self, context):\n        return context["execution_date"]\n',
     'class Op(BaseOperator):\n    def execute(self, context):\n        return context["logical_date"]\n'),
    ('def f():\n    ctx = get_current_context()\n    return ctx["execution_date"]\n',
     'def f():\n    ctx = get_current_context()\n    return ctx["logical_date"]\n'),
    ('def f(**c):\n    return c["yesterday_ds"]\n',
     'def f(**c):\n    return c["macros"].ds_add(c["ds"], -1)\n'),
    ('def f(**c):\n    return c["tomorrow_ds_nodash"]\n',
     'def f(**c):\n    return c["macros"].ds_format(c["macros"].ds_add(c["ds"], 1), "%Y-%m-%d", "%Y%m%d")\n'),
    ('def f(**c):\n    return c["triggering_dataset_events"]\n',
     'def f(**c):\n    return c["triggering_asset_events"]\n'),
    ('def f(dag_run):\n    return dag_run.execution_date\n', 'def f(dag_run):\n    return dag_run.logical_date\n'),
])
def test_context_keys_rewritten(src, expected):
    path = Path("dag.py")
    assert CTX.find(path, src, OPTS, "llm") == []
    assert CTX.apply(path, src, OPTS) == expected


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
    assert CTX.apply(Path("d.py"), src, OPTS) == src.replace('ctx["execution_date"]', 'ctx["logical_date"]')


def test_unrelated_lookups_untouched():
    src = 'def f(**context):\n    return context["ds"], context["logical_date"], d["other"]\n'
    assert CTX.find(Path("dag.py"), src, OPTS, "deterministic") == [] and CTX.apply(Path("dag.py"), src, OPTS) == src


# --- templates -----------------------------------------------------------------

@pytest.mark.parametrize("src, expected", [
    ('cmd = "echo {{ execution_date }}"\n', 'cmd = "echo {{ logical_date }}"\n'),
    ('cmd = "{{ execution_date.strftime(\'%Y\') }} {{ ds }}"\n', 'cmd = "{{ logical_date.strftime(\'%Y\') }} {{ ds }}"\n'),
    # the replacement's quotes flip to fit inside a "..." literal
    ('cmd = "{{ yesterday_ds_nodash }}"\n',
     "cmd = \"{{ macros.ds_format(macros.ds_add(ds, -1), '%Y-%m-%d', '%Y%m%d') }}\"\n"),
    ("cmd = '{{ yesterday_ds_nodash }}'\n",
     'cmd = \'{{ macros.ds_format(macros.ds_add(ds, -1), "%Y-%m-%d", "%Y%m%d") }}\'\n'),
    ('sql = """\nWHERE d = \'{{ tomorrow_ds }}\'\n"""\n', 'sql = """\nWHERE d = \'{{ macros.ds_add(ds, 1) }}\'\n"""\n'),
    ('cmd = "{% if execution_date %}x{% endif %} {{ dag_run.execution_date }}"\n',
     'cmd = "{% if logical_date %}x{% endif %} {{ dag_run.logical_date }}"\n'),
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
    assert TPL.apply(Path("run.sh"), sh, OPTS) == "echo {{ logical_date }} $execution_date\necho {{ next_ds }}\n"
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
    assert 'context["logical_date"]' in etl and "execution_date" not in etl
    assert 'schedule=CronDataIntervalTimetable("@daily", timezone="UTC")' in etl
    assert "{{ logical_date.strftime('%Y-%m-%d') }}" in (repo / "dags" / "scripts" / "report.sh").read_text()
    assert "{{ macros.ds_add(ds, 1) }}" in (repo / "dags" / "sql" / "summary.sql").read_text()
    # judgment sites are untouched and listed, each with its reason
    part = (repo / "dags" / "partitions.py").read_text()
    assert 'ctx["execution_date"]' in part and "{{ next_ds }}" in part
    assert sorted((m.path, m.rule_id) for m in result.manual) == [
        ("dags/partitions.py", "airflow3-context-judgment"), ("dags/partitions.py", "airflow3-template-judgment")]
    ruff_rows = [r for r in result.rows if r.rule_id.startswith("ruff ")]
    assert all(r.note.startswith("fixed by ruff AIR3") for r in ruff_rows)
    assert "fixed by ruff AIR301" in result.body and "## api-change-agent: Airflow 2 → 3" in result.body


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

FALLBACK = 'context["logical_date"] or context["dag_run"].run_after'


def _manual_runs(body):
    return body.split("### ⚠️ Manual runs", 1)[1].split("\n### ", 1)[0]


def test_manual_runs_warning_lists_every_execution_date_site(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(AIRFLOW_REPO, repo)
    result = run(Options(repo=repo, rules_path=AIRFLOW_RULES, dry_run=True, test_command=None,
                         repo_slug="acme/dags", branch="api-change-agent/airflow"), model=None)
    section = _manual_runs(result.body)
    blob = "https://github.com/acme/dags/blob/api-change-agent/airflow"
    for path, line in [("dags/etl_daily.py", 11), ("dags/etl_daily.py", 17), ("dags/etl_daily.py", 35),
                       ("dags/scripts/report.sh", 3), ("dags/sql/summary.sql", 3)]:
        assert f"[`{path}:{line}`]({blob}/{path}#L{line})" in section
    assert "These 5 line(s) now read `logical_date` where they read `execution_date`" in section
    assert "etl_daily.py:18" not in section          # yesterday_ds -> macros.ds_add: no logical_date
    assert "partitions.py" not in section            # left for judgment, not changed
    assert FALLBACK in section and "{{ logical_date or dag_run.run_after }}" in section
    assert "can have **no logical date**" in section
    # the warning is in the PR body, and the code itself is not guarded
    assert "run_after" not in (repo / "dags" / "etl_daily.py").read_text()


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
    assert "`dags/partitions.py:12`" in section.split("in place of another removed key")[0]  # execution_date -> logical_date
    assert "`dags/partitions.py:21`" in section.split("in place of another removed key")[1]  # next_ds -> logical_date + 1h
    assert "These 6 line(s)" in section and "These 1 line(s)" in section


def test_no_manual_runs_section_for_openai(sample_repo):
    from conftest import RULES as OPENAI_RULES
    result = run(Options(repo=sample_repo, rules_path=OPENAI_RULES, dry_run=True, test_command=None), model=None)
    assert "Manual runs" not in result.body
