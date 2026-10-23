"""The four gaps the real-world scorecard found (research/airflow-real-world/findings.md).

1. days_ago(N) -> pendulum.today("UTC").add(days=-N): the same instant, still evaluated at parse time.
2. The cron pin reads start_date from default_args (Airflow 2's own precedence).
3. Every cron DAG left unpinned is render-checked as a whole and named in a PR warning.
4. A file that fails on Airflow 3 before and after: no unsafe ruff fixes, and listed as unverified.
"""
import datetime as dt
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from api_change_agent.agent import Options, run
from api_change_agent.plugins import PLUGINS
from conftest import FIXTURES, ROOT

AIRFLOW_RULES = ROOT / "rules" / "airflow.yaml"
AIRFLOW_REPO = FIXTURES / "airflow_repo"
DAYS_AGO, PIN = PLUGINS["airflow_days_ago"], PLUGINS["airflow_cron_pin"]
AF2, AF3 = os.environ.get("ACA_AIRFLOW2_VENV"), os.environ.get("ACA_AIRFLOW3_VENV")
needs_af2 = pytest.mark.skipif(not AF2, reason="set ACA_AIRFLOW2_VENV")
needs_af3 = pytest.mark.skipif(not AF3, reason="set ACA_AIRFLOW3_VENV")
needs_both = pytest.mark.skipif(not (AF2 and AF3), reason="set ACA_AIRFLOW2_VENV and ACA_AIRFLOW3_VENV")


def code(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


# --- 1. days_ago ---------------------------------------------------------------------------

@pytest.mark.parametrize("src, expected", [
    (code('''
        from airflow import DAG
        from airflow.utils.dates import days_ago

        DAG("d", start_date=days_ago(2))
     '''), code('''
        from airflow import DAG
        import pendulum

        DAG("d", start_date=pendulum.today("UTC").add(days=-2))
     ''')),
    (code('''
        import pendulum
        from airflow.utils.dates import days_ago, cron_presets

        x = [days_ago(0), days_ago(1, hour=6, minute=30), days_ago(-1)]
     '''), code('''
        import pendulum
        from airflow.utils.dates import cron_presets

        x = [pendulum.today("UTC").add(days=0), pendulum.today("UTC").add(days=-1, hours=6, minutes=30), pendulum.today("UTC").add(days=1)]
     ''')),
    (code('''
        from airflow.utils import dates

        default_args = {"start_date": dates.days_ago(1)}
     '''), code('''
        import pendulum

        default_args = {"start_date": pendulum.today("UTC").add(days=-1)}
     ''')),
    (code('''
        import airflow.utils.dates

        start = airflow.utils.dates.days_ago(3)
     '''), code('''
        import pendulum

        start = pendulum.today("UTC").add(days=-3)
     ''')),
])
def test_days_ago_rewritten(src, expected):
    assert DAYS_AGO.find(Path("d.py"), src, {}, "llm") == []
    assert DAYS_AGO.apply(Path("d.py"), src, {}) == expected


@pytest.mark.parametrize("src, reason_has", [
    ('from airflow.utils.dates import days_ago\nN = 2\nx = days_ago(N)\n', "not a literal"),
    ('from airflow.utils.dates import days_ago\nx = days_ago(1, hour=h)\n', "not a literal"),
    ('from airflow.utils.dates import days_ago\npendulum = None\nx = days_ago(1)\n', "pendulum"),
])
def test_days_ago_left_to_judgment(src, reason_has):
    (site,) = DAYS_AGO.find(Path("d.py"), src, {}, "llm")
    assert reason_has in site.reason
    assert DAYS_AGO.apply(Path("d.py"), src, {}) == src


CASES = [(0, {}), (2, {}), (1, {"hour": 6, "minute": 30}), (7, {"hour": 23, "second": 5, "microsecond": 7}), (-1, {})]


def _evaluate(venv, expressions: list[str], imports: str) -> list[str]:
    script = imports + "\nfor e in %r:\n    print(eval(e).isoformat())\n" % expressions
    out = subprocess.run([str(Path(venv) / "bin" / "python"), "-W", "ignore", "-c", script],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout.split()


@needs_both
def test_days_ago_replacement_is_the_same_instant():
    """Airflow 2's days_ago and our replacement, each on its own version, at the same moment."""
    calls = [f"days_ago({n}{''.join(f', {k}={v}' for k, v in kw.items())})" for n, kw in CASES]
    replaced = [DAYS_AGO.apply(Path("d.py"), f"from airflow.utils.dates import days_ago\nx = {c}\n", {})
                .split("x = ", 1)[1].strip() for c in calls]
    for _ in range(3):  # don't straddle midnight UTC
        day = dt.datetime.now(dt.timezone.utc).date()
        old = _evaluate(AF2, calls, "from airflow.utils.dates import days_ago")
        new = _evaluate(AF3, replaced, "import pendulum")
        if dt.datetime.now(dt.timezone.utc).date() == day:
            break
    assert old == new and len(old) == len(CASES)
    assert dt.datetime.fromisoformat(old[0]) == dt.datetime.combine(day, dt.time(), dt.timezone.utc)  # UTC midnight


# --- 2. the pin reads default_args -----------------------------------------------------------

HEAD = "import datetime\nimport pendulum\nfrom airflow.sdk import DAG\n"


@pytest.mark.parametrize("body, tz", [
    ('default_args = {"owner": "x", "start_date": datetime.datetime(2024, 1, 1)}\n'
     'DAG("d", default_args=default_args, schedule="@daily")\n', "Europe/London"),          # naive -> default zone
    ('default_args = {"start_date": pendulum.datetime(2024, 1, 1, tz="Asia/Tokyo")}\n'
     'DAG("d", default_args=default_args, schedule="@daily")\n', "Asia/Tokyo"),
    ('default_args = {"start_date": pendulum.datetime(2024, 1, 1, tz="Asia/Tokyo")}\n'      # Airflow 2: a naive DAG
     'DAG("d", default_args=default_args, start_date=datetime.datetime(2024, 1, 1), schedule="@daily")\n',
     "Asia/Tokyo"),                                                                          # start_date defers to it
    ('default_args = {"start_date": pendulum.datetime(2024, 1, 1, tz="Asia/Tokyo")}\n'
     'DAG("d", default_args=default_args, start_date=pendulum.datetime(2024, 1, 1, tz="America/Lima"), schedule="@daily")\n',
     "America/Lima"),                                                                        # an aware one wins
    ('DAG("d", default_args=dict(owner="x", start_date=pendulum.today("UTC").add(days=-1)), schedule="0 4 * * *")\n',
     "UTC"),
    ('START = pendulum.datetime(2024, 1, 1, tz="Asia/Tokyo")\n'
     'DAG("d", start_date=START, schedule="@daily")\n', "Asia/Tokyo"),
    ('from airflow.utils.dates import days_ago\n'
     'DAG("d", default_args={"start_date": days_ago(1)}, schedule="@daily")\n', "UTC"),
])
def test_pin_reads_start_date_from_default_args(body, tz):
    out = PIN.apply(Path("d.py"), HEAD + body, {"default_timezone": "Europe/London"})
    assert f'timezone="{tz}")' in out, out


@pytest.mark.parametrize("body", [
    'default_args = {"start_date": datetime.datetime(2024, 1, 1)}\ndefault_args["start_date"] = x\n'
    'DAG("d", default_args=default_args, schedule="@daily")\n',                              # mutated
    'default_args = {**base, "owner": "x"}\nDAG("d", default_args=default_args, schedule="@daily")\n',  # spread
    'DAG("d", default_args=make_args(), schedule="@daily")\n',                               # computed
    'DAG("d", schedule="@daily")\n',                                                         # no start_date at all
    'default_args = {"start_date": pendulum.today().add(days=-1)}\n'                        # local zone: unknowable
    'DAG("d", default_args=default_args, schedule="@daily")\n',
])
def test_pin_leaves_unreadable_default_args_to_judgment(body):
    src = HEAD + body
    assert PIN.apply(Path("d.py"), src, {"default_timezone": "UTC"}) == src
    (site,) = PIN.find(Path("d.py"), src, {"default_timezone": "UTC"}, "llm")
    assert "shift" in site.reason  # says what happens if it stays unpinned


# --- agent-level --------------------------------------------------------------------------------

def agent(tmp_path, extra: dict, *, venvs=False, **kw):
    repo = tmp_path / "repo"
    shutil.copytree(AIRFLOW_REPO, repo)
    for rel, text in extra.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(code(text))
    opts = dict(repo=repo, rules_path=AIRFLOW_RULES, dry_run=True, test_command=None, log=lambda *_: None)
    if venvs:
        opts.update(test_command="auto", install_command=None, venv_dir=Path(AF3))
        if AF2:
            opts["render_old_venv"] = Path(AF2)
    opts.update(kw)
    return repo, run(Options(**opts), model=None)


RELATIVE = '''
    from airflow import DAG
    from airflow.utils.dates import days_ago
    from airflow.operators.bash_operator import BashOperator

    with DAG("relative", schedule_interval="@daily", start_date=days_ago(2), catchup=False) as dag:
        BashOperator(task_id="t", bash_command="echo {{ ds }}")
'''
UNPINNED = '''
    import datetime
    from airflow import DAG
    from airflow.operators.bash_operator import BashOperator

    SCHEDULE = "0 4 * * *"
    with DAG("unpinned", schedule_interval=SCHEDULE, start_date=datetime.datetime(2024, 1, 1), catchup=False) as dag:
        BashOperator(task_id="t", bash_command="run --date={{ ds }}")
'''
BROKEN = '''
    import not_installed_xyz
    from airflow import DAG
    from airflow.operators.bash_operator import BashOperator

    with DAG("broken", schedule_interval=None) as dag:
        BashOperator(task_id="t", bash_command="x")
'''


def test_days_ago_is_not_left_on_the_manual_list(tmp_path):
    repo, result = agent(tmp_path, {"dags/relative.py": RELATIVE})
    text = (repo / "dags" / "relative.py").read_text()
    assert "days_ago" not in text and 'start_date=pendulum.today("UTC").add(days=-2)' in text
    assert 'CronDataIntervalTimetable("@daily", timezone="UTC")' in text  # days_ago is UTC-based
    assert not [m for m in result.manual if "days_ago" in m.reason]   # ruff's "no automatic fix" item is gone


@needs_both
def test_days_ago_and_its_pin_render_identically(tmp_path):
    repo, result = agent(tmp_path, {"dags/relative.py": RELATIVE}, venvs=True)
    rows = [r for r in result.rows if r.path == "dags/relative.py" and r.rule_id in
            ("airflow3-days-ago", "airflow3-cron-timetable-pin")]
    assert {r.rule_id for r in rows} == {"airflow3-days-ago", "airflow3-cron-timetable-pin"}
    assert all("identical across" in r.note for r in rows), [r.note for r in rows]
    assert "dags/relative.py::import" not in result.tests_after.failures


def test_unpinned_cron_dags_get_a_warning(tmp_path):
    repo, result = agent(tmp_path, {"dags/unpinned.py": UNPINNED})
    warning = result.body.split("### ⚠️ Run dates will shift", 1)[1].split("\n### ", 1)[0]
    assert "`dags/unpinned.py:6`" in warning and "create_cron_data_intervals" in warning
    (m,) = [m for m in result.manual if m.path == "dags/unpinned.py" and m.rule_id == "airflow3-timetable-judgment"]
    assert "shift" in m.reason


@needs_both
def test_unpinned_cron_dags_are_render_checked_whole(tmp_path):
    repo, result = agent(tmp_path, {"dags/unpinned.py": UNPINNED}, venvs=True)
    (res,) = [r for r in result.render_results if r.dag_id == "unpinned"]
    assert res.mismatches and "--date=" in res.mismatches[0].old  # shifted by a day: shown, not hidden
    warning = result.body.split("### ⚠️ Run dates will shift", 1)[1].split("\n### ", 1)[0]
    assert "render mismatch" in warning
    assert 'CronDataIntervalTimetable("@daily"' in (repo / "dags" / "etl_daily.py").read_text()  # other pins kept


@needs_af3
def test_file_failing_before_and_after_is_unverified_and_gets_no_unsafe_fixes(tmp_path):
    repo, result = agent(tmp_path, {"dags/broken.py": BROKEN}, venvs=True)
    text = (repo / "dags" / "broken.py").read_text()
    assert "from airflow import DAG" in text and "airflow.operators.bash_operator" in text  # unsafe: not applied
    assert "schedule=None" in text                                                           # safe: applied
    (u,) = [u for u in result.unverified if u.path == "dags/broken.py"]
    assert "not_installed_xyz" in u.error
    rows = [r for r in result.rows if r.path == "dags/broken.py"]
    assert rows and all("unverified" in r.note for r in rows)
    section = result.body.split("### ⚠️ Unverified", 1)[1].split("\n### ", 1)[0]
    assert "`dags/broken.py`" in section and "not_installed_xyz" in section
    assert any(m.path == "dags/broken.py" and "unsafe fix not applied" in m.reason for m in result.manual)
    assert "dags/etl_daily.py::import" not in result.tests_after.failures  # verified files keep their fixes
