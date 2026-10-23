"""Mutation tests for the render check: deliberately wrong migrations must never read as "identical".

Each mutant is an Airflow 2 DAG (`old`, rendered on 2.11.2) and a wrong "migration" of it (`new`, rendered on
3.3.2). The check must report the mutated field as a mismatch or as not render-checked. Controls are correct
migrations and must read as identical, so the suite can't pass by flagging everything.

All DAGs render in one call per version (two small repos), to keep this fast enough for CI.
"""
import os
import textwrap
from pathlib import Path

import pytest

from api_change_agent import render_check

AF2, AF3 = os.environ.get("ACA_AIRFLOW2_VENV"), os.environ.get("ACA_AIRFLOW3_VENV")
pytestmark = pytest.mark.skipif(not (AF2 and AF3), reason="set ACA_AIRFLOW2_VENV and ACA_AIRFLOW3_VENV")

AF2_HEAD = "import datetime\nimport pendulum\nfrom airflow import DAG\nfrom airflow.operators.bash import BashOperator\n"
AF3_HEAD = ("import datetime\nimport pendulum\nfrom airflow.sdk import DAG, CronDataIntervalTimetable, "
            "DeltaDataIntervalTimetable\nfrom airflow.providers.standard.operators.bash import BashOperator\n")
START = "datetime.datetime(2024, 1, 1)"


def dag(head, dag_id, schedule, command, start=START, extra=""):
    return head + textwrap.dedent(f"""
        with DAG("{dag_id}", schedule={schedule}, start_date={start}, catchup=False{extra}):
            BashOperator(task_id="t", bash_command={command!r})
    """)


def pin(cron, tz="UTC"):
    return f'CronDataIntervalTimetable("{cron}", timezone="{tz}")'


# name -> (Airflow 2 schedule, Airflow 2 command, Airflow 3 schedule, Airflow 3 command[, start_date on both])
MUTANTS = {
    # dates shifted by one interval
    "unpinned_daily": ('"@daily"', "run {{ ds }}", '"@daily"', "run {{ ds }}"),
    "unpinned_hourly_ts": ('"@hourly"', "run {{ ts }}", '"@hourly"', "run {{ ts }}"),
    "pinned_but_plus_one_day": ('"@daily"', "run {{ ds }}", pin("@daily"), "run {{ macros.ds_add(ds, 1) }}"),
    "yesterday_ds_wrong_sign": ('"@daily"', "run {{ yesterday_ds }}", pin("@daily"), "run {{ macros.ds_add(ds, 1) }}"),
    # wrong timezone on the pin (the DAG's start_date is naive: default_timezone, UTC)
    "pin_wrong_tz_midnight": ('"0 0 * * *"', "run {{ ds }}", pin("0 0 * * *", "Europe/London"), "run {{ ds }}"),
    "pin_wrong_tz_23h": ('"0 23 * * *"', "run {{ ds }}", pin("0 23 * * *", "America/New_York"), "run {{ ds }}"),
    # a template variable dropped or swapped
    "dropped_var": ('"@daily"', "run {{ ds }} --to {{ next_ds }}", pin("@daily"), "run {{ ds }} --to"),
    "next_ds_as_ds": ('"@daily"', "run {{ next_ds }}", pin("@daily"), "run {{ ds }}"),
    "ds_as_interval_end": ('"@daily"', "run {{ ds }}", pin("@daily"), "run {{ data_interval_end | ds }}"),
    "ds_nodash_as_ds": ('"@daily"', "run {{ ds_nodash }}", pin("@daily"), "run {{ ds }}"),
    "only_wrong_late_evening": ('"@hourly"', "run {{ next_ds }}", pin("@hourly"),  # +1h would be right when pinned
                                "run {{ (logical_date + macros.timedelta(hours=2)) | ds }}"),
    "only_wrong_on_leap_day": ('"@daily"', "run {{ ds }}", pin("@daily"),
                               "run {{ '2024-03-01' if ds == '2024-02-29' else ds }}"),
    # runs that don't pair, fully or partly
    "schedule_moved_an_hour": ('"0 1 * * *"', "run {{ ds }}", pin("0 2 * * *"), "run {{ ds }}"),
    "every_other_hour": ('"@hourly"', "run {{ ts }}", pin("0 */2 * * *"), "run {{ ts }}"),
    "timedelta_unpinned": ("datetime.timedelta(days=1)", "run {{ ds }}", "datetime.timedelta(days=1)", "run {{ ds }}"),
    "unscheduled_wrong": ("None", "run {{ ds }}", "None", "run {{ macros.ds_add(ds, 1) }}"),
    # fails to render on Airflow 3
    "removed_key_left": ('"@daily"', "run {{ next_ds }}", pin("@daily"), "run {{ next_ds }}"),
}
CONTROLS = {
    "pinned_daily": ('"@daily"', "run {{ ds }}", pin("@daily"), "run {{ ds }}"),
    "pinned_next_ds": ('"@daily"', "run {{ next_ds }}", pin("@daily"), "run {{ data_interval_end | ds }}"),
    "pinned_yesterday": ('"@daily"', "run {{ yesterday_ds }}", pin("@daily"), "run {{ macros.ds_add(ds, -1) }}"),
    "pinned_timedelta": ("datetime.timedelta(days=1)", "run {{ ds }}", "DeltaDataIntervalTimetable(datetime.timedelta(days=1))",
                         "run {{ ds }}"),
    "pinned_hourly_next": ('"@hourly"', "run {{ next_ds }}", pin("@hourly"), "run {{ data_interval_end | ds }}"),
    "pinned_hourly_plus_1h": ('"@hourly"', "run {{ next_ds }}", pin("@hourly"),
                              "run {{ (logical_date + macros.timedelta(hours=1)) | ds }}"),
    "pinned_london_dst": ('"0 0 * * *"', "run {{ ds }}", pin("0 0 * * *", "Europe/London"), "run {{ ds }}",
                          'pendulum.datetime(2024, 1, 1, tz="Europe/London")'),
}


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    root = tmp_path_factory.mktemp("mutants")
    for side, head, at in (("old", AF2_HEAD, 0), ("new", AF3_HEAD, 2)):
        (root / side / "dags").mkdir(parents=True)
        for name, spec in {**MUTANTS, **CONTROLS}.items():
            start = spec[4] if len(spec) > 4 else START
            (root / side / "dags" / f"{name}.py").write_text(dag(head, name, spec[at], spec[at + 1], start=start))
    files = [f"dags/{n}.py" for n in {**MUTANTS, **CONTROLS}]
    old = render_check.render(Path(AF2) / "bin" / "python", root / "old", files)
    new = render_check.render(Path(AF3) / "bin" / "python", root / "new", files)
    assert not old["errors"] and not new["errors"], (old["errors"], new["errors"])
    out = render_check.compare(old, new, changed=set(), whole_dags=set(new["dags"]))
    return {r.dag_id: r for r in out if r.field == "bash_command"}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_wrong_migration_is_never_identical(results, name):
    res = results.get(name)
    assert res is not None, f"{name}: not compared at all"
    verdict = render_check.summary(res)
    assert res.mismatches or res.unpaired, f"{name} read as identical: {verdict}"
    assert not verdict.startswith("render-checked: identical")


@pytest.mark.parametrize("name", sorted(CONTROLS))
def test_correct_migration_is_identical(results, name):
    res = results[name]
    assert not res.mismatches and not res.unpaired and res.runs >= 20, render_check.summary(res)
