"""A naive start_date means Airflow's [core] default_timezone; the pin and render check must use the repo's real one."""
import shutil
from pathlib import Path

import pytest

from api_change_agent.agent import Options, run
from api_change_agent.airflow_config import find_default_timezone
from api_change_agent.plugins import PLUGINS
from conftest import FIXTURES, ROOT
from test_render_check import AF2, AF3, AIRFLOW_REPO, needs_both

AIRFLOW_RULES = ROOT / "rules" / "airflow.yaml"
PIN = PLUGINS["airflow_cron_pin"]
NAIVE = 'import datetime\nfrom airflow.sdk import DAG\nDAG("d", schedule="@daily", start_date=datetime.datetime(2024, 1, 1))\n'


def write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)


@pytest.mark.parametrize("rel, text", [
    ("airflow.cfg", "[core]\ndags_folder = /dags\ndefault_timezone = Europe/London\n[webserver]\ndefault_timezone = x\n"),
    ("config/airflow.cfg", "[core]\n# default_timezone = Asia/Tokyo\ndefault_timezone=Europe/London\n"),
    (".env", "AIRFLOW_UID=50000\nAIRFLOW__CORE__DEFAULT_TIMEZONE=Europe/London\n"),
    ("deploy/airflow.env", "export AIRFLOW__CORE__DEFAULT_TIMEZONE='Europe/London'\n"),
    ("docker-compose.yaml", "x-airflow-common:\n  environment:\n    AIRFLOW__CORE__DEFAULT_TIMEZONE: 'Europe/London'\n"),
    ("docker-compose.yml", "services:\n  s:\n    environment:\n      - AIRFLOW__CORE__DEFAULT_TIMEZONE=Europe/London\n"),
    ("Dockerfile", "FROM apache/airflow:2.9.3\nENV AIRFLOW__CORE__DEFAULT_TIMEZONE=Europe/London\n"),
    ("docker/Dockerfile.airflow", "FROM apache/airflow:2.9.3\nENV AIRFLOW__CORE__DEFAULT_TIMEZONE Europe/London\n"),
    ("helm/values.yaml", "env:\n  - name: AIRFLOW__CORE__DEFAULT_TIMEZONE\n    value: \"Europe/London\"\n"),
    ("chart/values-prod.yaml", "config:\n  core:\n    default_timezone: Europe/London\n"),
])
def test_finds_the_setting(tmp_path, rel, text):
    write(tmp_path, rel, text)
    found = find_default_timezone(tmp_path)
    assert found.tz == "Europe/London" and not found.problem
    assert [s.path for s in found.sources] == [rel] and found.sources[0].line > 0


def test_nothing_found(tmp_path):
    write(tmp_path, "dags/d.py", 'os.environ["AIRFLOW__CORE__DEFAULT_TIMEZONE"]\n')  # code, not config
    write(tmp_path, ".venv/lib/airflow.cfg", "[core]\ndefault_timezone = Asia/Tokyo\n")  # someone else's
    found = find_default_timezone(tmp_path)
    assert found.tz is None and found.sources == [] and not found.problem


def test_env_overrides_cfg_and_utc_is_normalised(tmp_path):
    write(tmp_path, "airflow.cfg", "[core]\ndefault_timezone = Asia/Tokyo\n")
    write(tmp_path, ".env", "AIRFLOW__CORE__DEFAULT_TIMEZONE=utc\n")
    assert find_default_timezone(tmp_path).tz == "UTC"


@pytest.mark.parametrize("files, problem_has", [
    ({".env": "AIRFLOW__CORE__DEFAULT_TIMEZONE=Europe/London\n",
      "Dockerfile": "ENV AIRFLOW__CORE__DEFAULT_TIMEZONE=Asia/Tokyo\n"}, "Asia/Tokyo"),
    ({"airflow.cfg": "[core]\ndefault_timezone = system\n"}, "system"),
    ({".env": "AIRFLOW__CORE__DEFAULT_TIMEZONE=Mars/Olympus\n"}, "Mars/Olympus"),
])
def test_unusable_settings_are_a_problem(tmp_path, files, problem_has):
    for rel, text in files.items():
        write(tmp_path, rel, text)
    found = find_default_timezone(tmp_path)
    assert found.tz is None and problem_has in found.problem


def test_pin_uses_the_default_timezone_for_naive_start_dates():
    out = PIN.apply(Path("d.py"), NAIVE, {"default_timezone": "Europe/London"})
    assert 'CronDataIntervalTimetable("@daily", timezone="Europe/London")' in out
    (site,) = PIN.find(Path("d.py"), NAIVE, {"default_timezone": "Europe/London"}, "deterministic")
    assert "naive start_date" in site.reason
    # an explicit zone on the start_date wins
    aware = NAIVE.replace("datetime.datetime(2024, 1, 1)", 'pendulum.datetime(2024, 1, 1, tz="Asia/Tokyo")')
    assert 'timezone="Asia/Tokyo"' in PIN.apply(Path("d.py"), aware, {"default_timezone": "Europe/London"})


def test_pin_leaves_naive_dags_to_judgment_when_the_setting_is_unusable():
    opts = {"default_timezone": None, "timezone_problem": "conflicting values: Europe/London, Asia/Tokyo"}
    assert PIN.apply(Path("d.py"), NAIVE, opts) == NAIVE
    (site,) = PIN.find(Path("d.py"), NAIVE, opts, "llm")
    assert "conflicting values" in site.reason


def agent(tmp_path, extra: dict, **kw):
    repo = tmp_path / "repo"
    shutil.copytree(AIRFLOW_REPO, repo)
    for rel, text in extra.items():
        write(repo, rel, text)
    kw = {"test_command": None, **kw}
    return repo, run(Options(repo=repo, rules_path=AIRFLOW_RULES, dry_run=True, log=lambda *_: None, **kw), model=None)


def test_agent_pins_to_the_repo_timezone_and_says_where_it_came_from(tmp_path):
    repo, result = agent(tmp_path, {"docker-compose.yaml": "x:\n  environment:\n"
                                                           "    AIRFLOW__CORE__DEFAULT_TIMEZONE: Europe/London\n"})
    assert 'CronDataIntervalTimetable("@daily", timezone="Europe/London")' in (repo / "dags/etl_daily.py").read_text()
    assert "### ⚠️ Timezone assumed" not in result.body
    assert "`default_timezone = Europe/London`" in result.body and "docker-compose.yaml:3" in result.body


def test_agent_warns_loudly_when_it_assumes_utc(tmp_path):
    repo, result = agent(tmp_path, {})
    assert 'timezone="UTC"' in (repo / "dags/etl_daily.py").read_text()
    warning = result.body.split("### ⚠️ Timezone assumed: UTC", 1)[1].split("\n### ", 1)[0]
    assert "default_timezone" in warning and "AIRFLOW__CORE__DEFAULT_TIMEZONE" in warning
    assert "`dags/etl_daily.py:27`" in warning and "`dags/partitions.py:19`" in warning


def test_agent_leaves_naive_dags_manual_on_conflicting_settings(tmp_path):
    repo, result = agent(tmp_path, {".env": "AIRFLOW__CORE__DEFAULT_TIMEZONE=Europe/London\n",
                                    "Dockerfile": "ENV AIRFLOW__CORE__DEFAULT_TIMEZONE=Asia/Tokyo\n"})
    assert "CronDataIntervalTimetable" not in (repo / "dags/etl_daily.py").read_text()
    assert any("Asia/Tokyo" in m.reason for m in result.manual if m.rule_id == "airflow3-timetable-judgment")


@needs_both
def test_render_check_runs_in_the_repo_timezone(tmp_path):
    repo, result = agent(tmp_path, {"airflow.cfg": "[core]\ndefault_timezone = Europe/London\n"},
                         test_command="auto", install_command=None, venv_dir=Path(AF3), render_old_venv=Path(AF2))
    pins = [r for r in result.rows if r.rule_id == "airflow3-cron-timetable-pin" and r.path == "dags/etl_daily.py"]
    assert pins and all("identical across" in r.note and "DST: 2024-03-31, 2024-10-27" in r.note for r in pins), \
        [r.note for r in pins]
    checked = [r for r in result.render_results if r.file == "dags/etl_daily.py"]
    assert checked and all(not r.mismatches for r in checked)  # partitions' next_ds waits for Claude (none here)
    partitions = next(r for r in result.rows if r.rule_id == "airflow3-cron-timetable-pin" and "partitions" in r.path)
    assert "not render-checked (no template field of this DAG could be compared" in partitions.note
