"""`[scheduler] create_cron_data_intervals = True` keeps Airflow 2's run dates globally: migrate mode must not pin.

Regression (real repo, 2026-11-08): CityofToronto/bdit_data-sources sets it in airflow.cfg, yet migrate mode
added 48 CronDataIntervalTimetable pins and its report said the flag was "now False". Verify mode already
honoured it (verify.py); migrate mode never read it.
"""
from pathlib import Path

import pytest
from test_default_timezone import agent
from test_render_check import AF2, AF3, needs_both

PIN_RULES = {"airflow3-cron-timetable-pin", "airflow3-timetable-judgment"}


@pytest.mark.parametrize("rel, text, where", [
    ("airflow.cfg", "[core]\nload_examples = False\n[scheduler]\ncreate_cron_data_intervals = True\n", "airflow.cfg:4"),
    ("docker-compose.yaml", ("x-airflow-common:\n  environment:\n"
                             "    AIRFLOW__SCHEDULER__CREATE_CRON_DATA_INTERVALS: 'true'\n"), "docker-compose.yaml:3"),
    (".env", "AIRFLOW__SCHEDULER__CREATE_CRON_DATA_INTERVALS=True\n", ".env:1"),
])
def test_flag_set_means_no_pins(tmp_path, rel, text, where):
    repo, result = agent(tmp_path, {rel: text})
    for dag in ("dags/etl_daily.py", "dags/partitions.py"):
        assert "CronDataIntervalTimetable" not in (repo / dag).read_text()
    assert not [r for r in result.rows if r.rule_id in PIN_RULES]
    assert not [m for m in result.manual if m.rule_id in PIN_RULES]
    note = next(n for n in result.skipped if "create_cron_data_intervals" in n)
    assert where in note and "no schedules were pinned" in note
    assert "now False" not in result.body and "### Timetable pin" not in result.body
    assert "Timezone assumed" not in result.body  # nothing was pinned, so no zone was assumed
    assert 'schedule="@daily"' in (repo / "dags/etl_daily.py").read_text()  # ruff's AIR301 fix still applied


@pytest.mark.parametrize("files", [
    {"airflow.cfg": "[scheduler]\ncreate_cron_data_intervals = False\n"},
    {"airflow.cfg": "[core]\ncreate_cron_data_intervals = True\n"},  # wrong section: Airflow doesn't read it
    {"airflow.cfg": "[scheduler]\n# create_cron_data_intervals = True\n"},
])
def test_flag_unset_or_false_still_pins(tmp_path, files):
    repo, result = agent(tmp_path, files)
    assert 'CronDataIntervalTimetable("@daily", timezone="UTC")' in (repo / "dags/etl_daily.py").read_text()
    assert not any("no schedules were pinned" in n for n in result.skipped)


@needs_both
def test_render_check_runs_airflow_3_with_the_repo_flag(tmp_path):
    # without pins, the Airflow 3 render must use the tree's own setting, or every {{ ds }} would mismatch
    repo, result = agent(tmp_path, {"airflow.cfg": "[scheduler]\ncreate_cron_data_intervals = True\n"},
                         test_command="auto", install_command=None, venv_dir=Path(AF3), render_old_venv=Path(AF2))
    assert "CronDataIntervalTimetable" not in (repo / "dags/etl_daily.py").read_text()
    checked = [r for r in result.render_results if r.file == "dags/etl_daily.py"]
    assert checked and all(not r.mismatches for r in checked), [r.mismatches[:1] for r in checked]
