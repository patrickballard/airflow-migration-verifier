"""Differential render check for Airflow 2 -> 3 template changes.

Old templates render on Airflow 2.11.2 (original code), new ones on Airflow 3.3.2
(migrated code), for runs each version would really schedule, paired by the
wall-clock moment they fire. Unit tests need no Airflow; the regression tests
need real venvs:

    ACA_AIRFLOW2_VENV=.../airflow-2 ACA_AIRFLOW3_VENV=.../airflow-3 pytest tests/test_render_check.py
"""
import datetime as dt
import os
import re
import shutil
from pathlib import Path

import pytest

from api_change_agent import airflow_render, render_check
from api_change_agent.plugins import PLUGINS
from api_change_agent.rules import load_rules
from conftest import FIXTURES, ROOT

AIRFLOW_RULES = ROOT / "rules" / "airflow.yaml"
AIRFLOW_REPO = FIXTURES / "airflow_repo"
PIN = PLUGINS["airflow_cron_pin"]
PIN_OPTS = next(r for r in load_rules(AIRFLOW_RULES).rules if r.id == "airflow3-cron-timetable-pin").options

RUN1 = "{{ data_interval_end | ds }}"                              # Claude, run 1
RUN2 = "{{ (logical_date + macros.timedelta(hours=1)) | ds }}"     # Claude, run 2


# --- sample runs ------------------------------------------------------------------

def test_targets_cover_the_edge_cases_in_utc():
    targets = airflow_render.targets("UTC", dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc), None)
    labels = [label for label, _ in targets]
    for needed in ("23:00", "00:00", "month end 23:00", "month start 00:00", "year end 23:00", "new year 00:00",
                   "leap day 00:00", "leap day 23:00"):
        assert needed in labels, needed
    assert sum(label.startswith("ordinary") for label in labels) == 20
    assert not [label for label in labels if label.startswith("DST")]  # UTC has no DST
    assert all(t.tzinfo is not None for _, t in targets)
    assert airflow_render.dst_note("UTC", 2024) == "DST: n/a (UTC has no DST)"


def test_targets_include_both_dst_switches():
    tz = "Europe/Amsterdam"
    labels = [label for label, _ in airflow_render.targets(tz, dt.datetime(2024, 1, 1), None)]
    assert {lbl.split(" ")[1] for lbl in labels if lbl.startswith("DST")} == {"2024-03-31", "2024-10-27"}
    assert airflow_render.dst_note(tz, 2024) == "DST: 2024-03-31, 2024-10-27"


def test_targets_respect_start_and_end_dates():
    start = dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc)
    end = dt.datetime(2024, 9, 1, tzinfo=dt.timezone.utc)
    assert all(start <= t <= end for _, t in airflow_render.targets("UTC", start, end))


# --- pairing and comparison -------------------------------------------------------------

def side(version, runs, fields):
    """A fake render result: one DAG, one task, field -> {raw, rendered per run}."""
    return {"version": version, "dags": {"d.py::d": {
        "file": "d.py", "dag_id": "d", "timetable": "X", "timezone": "UTC", "dst": "DST: n/a",
        "runs": [{"label": lbl, "fires": f, "logical": None, "interval": None} for lbl, f in runs],
        "tasks": {"t": {"fields": fields}}}}, "errors": {}}


def test_pairs_by_fire_time_not_by_position():
    old = side("2", [("23:00", "2024-01-15T23:00:00+00:00"), ("00:00", "2024-01-16T00:00:00+00:00")],
               {"cmd": {"raw": '"{{ next_ds }}"', "rendered": ["2024-01-15", "2024-01-16"]}})
    new = side("3", [("00:00", "2024-01-16T00:00:00+00:00"), ("23:00", "2024-01-15T23:00:00+00:00")],
               {"cmd": {"raw": f'"{RUN2}"', "rendered": ["2024-01-16", "2024-01-16"]}})
    (res,) = render_check.compare(old, new, changed={("d.py", "d", "t", "cmd")})
    assert res.runs == 2
    assert [(m.label, m.old, m.new) for m in res.mismatches] == [("23:00", "2024-01-15", "2024-01-16")]


def test_runs_that_need_the_task_runner_are_not_compared():
    # real repo: op_args=[task.output] can't resolve outside a task runner on Airflow 3
    runs = [("a", "2024-01-15T23:00:00+00:00"), ("b", "2024-01-16T23:00:00+00:00"), ("c", "2024-01-17T23:00:00+00:00")]
    needs = "RENDER ERROR ImportError: cannot import name 'SUPERVISOR_COMMS' from 'airflow.sdk.execution_time.task_runner'"
    old = side("2", runs, {"cmd": {"raw": '"x"', "rendered": ["A", "RENDER ERROR KeyError: v", "C"]},
                           "xcom": {"raw": '"x"', "rendered": ["[null]"] * 3, "unrenderable": "XComArg"}})
    new = side("3", runs, {"cmd": {"raw": '"y"', "rendered": [needs, "RENDER ERROR KeyError: v", "C"]},
                           "xcom": {"raw": '"x"', "rendered": [needs] * 3, "unrenderable": "XComArg"}})
    (res,) = render_check.compare(old, new, changed={("d.py", "d", "t", "cmd")}, whole_dags={"d.py::d"})
    assert res.field == "cmd" and res.runs == 1 and res.mismatches == []  # only run c: a needs the runner, b errs on both


def test_renamed_fields_are_compared_across_the_rename():
    runs = [("a", "2024-01-15T23:00:00+00:00")]
    old = side("2", runs, {"execution_date": {"raw": '"{{ ds }}"', "rendered": ["2024-01-15"]}})
    new = side("3", runs, {"logical_date": {"raw": '"{{ ds }}"', "rendered": ["2024-01-16"]}})
    (res,) = render_check.compare(old, new, changed=set(), whole_dags={"d.py::d"})
    assert res.field == "logical_date" and res.mismatches


def test_an_unset_trigger_logical_date_is_the_same_on_both_versions():
    # real repo: TriggerDagRunOperator without execution_date: Airflow 2 stores None, Airflow 3 its
    # NOTSET sentinel; both trigger the run "now"
    runs = [("a", "2024-01-15T23:00:00+00:00")]
    old = side("2", runs, {"execution_date": {"raw": "null", "rendered": ["null"]}})
    new = side("3", runs, {"logical_date": {"raw": '"NOTSET"', "rendered": ['"NOTSET"']}})
    (res,) = render_check.compare(old, new, changed=set(), whole_dags={"d.py::d"})
    assert res.mismatches == []
    other = side("3", runs, {"logical_date": {"raw": '"x"', "rendered": ['"NOTSET"']}})
    old2 = side("2", runs, {"execution_date": {"raw": '"x"', "rendered": ['"2024-01-15"']}})
    assert render_check.compare(old2, other, changed=set(), whole_dags={"d.py::d"})[0].mismatches  # only for unset


def test_mismatch_names_the_sample_not_the_fire_time():
    runs = [("23:00", "2024-01-16T09:00:00+00:00")]
    old = side("2", runs, {"cmd": {"raw": '"x"', "rendered": ["A"]}})
    new = side("3", runs, {"cmd": {"raw": '"y"', "rendered": ["B"]}})
    (res,) = render_check.compare(old, new, changed={("d.py", "d", "t", "cmd")})
    assert "the run firing 2024-01-16 09:00 UTC (from the 23:00 sample)" in render_check.summary(res)


def test_unchanged_fields_are_skipped_unless_the_dag_was_pinned():
    old = side("2", [("o", "2024-01-15T23:00:00+00:00")], {"cmd": {"raw": '"x"', "rendered": ["A"]}})
    new = side("3", [("o", "2024-01-15T23:00:00+00:00")], {"cmd": {"raw": '"x"', "rendered": ["B"]}})
    assert render_check.compare(old, new, changed=set()) == []
    (res,) = render_check.compare(old, new, changed=set(), whole_dags={"d.py::d"})
    assert res.mismatches


def test_changed_fields_detects_raw_and_template_file_changes():
    old = side("2", [], {"a": {"raw": '"x"', "rendered": []}, "b": {"raw": '"scripts/r.sh"', "rendered": []},
                         "c": {"raw": '"same"', "rendered": []}})
    new = side("3", [], {"a": {"raw": '"y"', "rendered": []}, "b": {"raw": '"scripts/r.sh"', "rendered": []},
                         "c": {"raw": '"same"', "rendered": []}})
    assert render_check.changed_fields(old, new, changed_files={"scripts/r.sh"}) == {
        ("d.py", "d", "t", "a"), ("d.py", "d", "t", "b")}


def test_summary_text():
    old = side("2", [("23:00", "2024-01-15T23:00:00+00:00")], {"c": {"raw": '"x"', "rendered": ["A"]}})
    (res,) = render_check.compare(old, old, changed={("d.py", "d", "t", "c")})
    # only the edge cases the paired runs really covered are named
    assert render_check.summary(res) == "render-checked: identical across 1 runs (incl. 23:00, DST: n/a)"


def test_task_spans():
    src = 'a = 1\nwith DAG("d") as dag:\n    t = BashOperator(\n        task_id="report",\n        bash_command="x",\n    )\n'
    assert render_check.task_spans(src) == {"report": (3, 6)}


# --- the timetable pin ------------------------------------------------------------------

@pytest.mark.parametrize("src, expected", [
    ('import datetime\nfrom airflow.sdk import DAG\n\nwith DAG("d", schedule="@daily", start_date=datetime.datetime(2024, 1, 1)) as dag:\n    pass\n',
     'import datetime\nfrom airflow.sdk import DAG, CronDataIntervalTimetable\n\nwith DAG("d", schedule=CronDataIntervalTimetable("@daily", timezone="UTC"), start_date=datetime.datetime(2024, 1, 1)) as dag:\n    pass\n'),
    ('import pendulum\nfrom airflow.sdk import DAG\nDAG("d", schedule="0 6 * * 1-5", start_date=pendulum.datetime(2024, 1, 1, tz="Europe/Amsterdam"))\n',
     'import pendulum\nfrom airflow.sdk import DAG, CronDataIntervalTimetable\nDAG("d", schedule=CronDataIntervalTimetable("0 6 * * 1-5", timezone="Europe/Amsterdam"), start_date=pendulum.datetime(2024, 1, 1, tz="Europe/Amsterdam"))\n'),
])
def test_pin_rewrites_cron_schedules(src, expected):
    assert PIN.find(Path("d.py"), src, PIN_OPTS, "llm") == []
    assert PIN.apply(Path("d.py"), src, PIN_OPTS) == expected


@pytest.mark.parametrize("src", [
    'from airflow.sdk import DAG\nDAG("d", schedule=None)\n',
    'from airflow.sdk import DAG\nDAG("d", schedule="@once")\n',
    'from airflow.sdk import DAG, Asset\nDAG("d", schedule=[Asset("s3://x")])\n',
    'from airflow.sdk import DAG, CronDataIntervalTimetable\nDAG("d", schedule=CronDataIntervalTimetable("@daily", timezone="UTC"))\n',
])
def test_pin_leaves_non_cron_schedules_alone(src):
    assert PIN.find(Path("d.py"), src, PIN_OPTS, "deterministic") == []
    assert PIN.apply(Path("d.py"), src, PIN_OPTS) == src


@pytest.mark.parametrize("src, reason_has", [
    ('import datetime\nfrom airflow.sdk import DAG\nDAG("d", schedule=datetime.timedelta(hours=1), start_date=datetime.datetime(2024, 1, 1))\n',
     "timedelta"),
    ('from airflow.sdk import DAG\nDAG("d", schedule="@daily", start_date=START)\n', "timezone"),
    ('from airflow.sdk import DAG\nDAG("d", schedule=CRON, start_date=None)\n', "not a literal"),
])
def test_pin_judgment_sites(src, reason_has):
    sites = PIN.find(Path("d.py"), src, PIN_OPTS, "llm")
    assert sites and reason_has in sites[0].reason
    assert PIN.apply(Path("d.py"), src, PIN_OPTS) == src


# --- regression, against real Airflow ----------------------------------------------------

AF2, AF3 = os.environ.get("ACA_AIRFLOW2_VENV"), os.environ.get("ACA_AIRFLOW3_VENV")
needs_both = pytest.mark.skipif(not (AF2 and AF3), reason="set ACA_AIRFLOW2_VENV and ACA_AIRFLOW3_VENV")


def migrated(tmp_path, name, *, next_ds=None, pin=False):
    """The fixture after our deterministic rewrites (+ an optional next_ds replacement and pin)."""
    from api_change_agent.agent import Options, run
    repo = tmp_path / name
    shutil.copytree(AIRFLOW_REPO, repo)
    rules = AIRFLOW_RULES
    if not pin:
        import yaml
        data = yaml.safe_load(AIRFLOW_RULES.read_text())
        data["rules"] = [r for r in data["rules"] if r["id"] != "airflow3-cron-timetable-pin"]
        rules = tmp_path / f"{name}.yaml"
        rules.write_text(yaml.safe_dump(data, sort_keys=False))
    run(Options(repo=repo, rules_path=rules, dry_run=True, test_command=None, log=lambda *_: None), model=None)
    p = repo / "dags" / "partitions.py"
    text = p.read_text().replace('ctx["execution_date"]', 'ctx["logical_date"]')
    if next_ds:
        text = text.replace("{{ next_ds }}", next_ds)
    p.write_text(text)
    return repo


def check(new_repo, files):
    old = render_check.render(Path(AF2) / "bin" / "python", AIRFLOW_REPO, files)
    new = render_check.render(Path(AF3) / "bin" / "python", new_repo, files)
    changed = render_check.changed_fields(old, new, changed_files={"scripts/report.sh", "sql/summary.sql"})
    return {(r.task_id, r.field): r for r in render_check.compare(old, new, changed=changed)}


@needs_both
def test_default_timetable_catches_run2_at_23h_and_passes_run1(tmp_path):
    r1 = check(migrated(tmp_path, "r1", next_ds=RUN1), ["dags/partitions.py"])[("next_window", "bash_command")]
    r2 = check(migrated(tmp_path, "r2", next_ds=RUN2), ["dags/partitions.py"])[("next_window", "bash_command")]
    assert r1.runs >= 25 and r1.mismatches == []
    assert [m.label for m in r2.mismatches] and all(m.fires.endswith("T23:00:00+00:00") for m in r2.mismatches)
    assert "23:00" in {m.label for m in r2.mismatches}


@needs_both
def test_default_timetable_catches_unpinned_cron_templates(tmp_path):
    res = check(migrated(tmp_path, "unpinned"), ["dags/etl_daily.py"])
    report = res[("report", "bash_command")]
    assert report.mismatches and len(report.mismatches) == report.runs  # shifted by a day on every run
    assert res[("script", "bash_command")].mismatches  # the .sh template file too


@needs_both
def test_with_the_pin_everything_is_identical(tmp_path):
    for name, next_ds in (("p1", RUN1), ("p2", RUN2)):
        repo = migrated(tmp_path, name, next_ds=next_ds, pin=True)
        assert "CronDataIntervalTimetable" in (repo / "dags" / "etl_daily.py").read_text()
        old = render_check.render(Path(AF2) / "bin" / "python", AIRFLOW_REPO, ["dags/etl_daily.py", "dags/partitions.py"])
        new = render_check.render(Path(AF3) / "bin" / "python", repo, ["dags/etl_daily.py", "dags/partitions.py"])
        results = render_check.compare(old, new, changed=set(), whole_dags=set(new["dags"]))  # every field
        assert results and all(r.mismatches == [] for r in results), [
            (r.task_id, r.field, r.mismatches[:1]) for r in results if r.mismatches]
        assert min(r.runs for r in results) >= 25


# --- the agent, end to end ----------------------------------------------------------------

def agent_run(tmp_path, name, *, pin=True, model=None):
    from api_change_agent.agent import Options, run
    import yaml
    repo = tmp_path / name
    shutil.copytree(AIRFLOW_REPO, repo)
    rules = AIRFLOW_RULES
    if not pin:
        data = yaml.safe_load(AIRFLOW_RULES.read_text())
        data["rules"] = [r for r in data["rules"] if r["id"] != "airflow3-cron-timetable-pin"]
        rules = tmp_path / f"{name}.yaml"
        rules.write_text(yaml.safe_dump(data, sort_keys=False))
    result = run(Options(repo=repo, rules_path=rules, dry_run=True, test_command="auto", install_command=None,
                         venv_dir=Path(AF3), render_old_venv=Path(AF2), log=lambda *_: None), model=model)
    return repo, result


class Replay:
    """Recorded Claude answers for the two judgment sites in partitions.py, one per attempt."""

    def __init__(self, *next_ds):
        self.next_ds, self.requests, self.last_notes = list(next_ds), [], "replayed"

    def propose(self, request):
        self.requests.append(request)
        if request.rule.id == "airflow3-context-judgment":
            return request.source.replace('ctx["execution_date"]', 'ctx["logical_date"]')
        attempt = sum(1 for r in self.requests if r.rule.id == request.rule.id)
        return request.source.replace("{{ next_ds }}", self.next_ds[min(attempt, len(self.next_ds)) - 1])


@needs_both
def test_agent_pins_and_render_checks_everything(tmp_path):
    repo, result = agent_run(tmp_path, "pinned", model=Replay(RUN2))
    rows = {(r.path, r.line): r for r in result.rows}
    pin_rows = [r for r in result.rows if r.rule_id == "airflow3-cron-timetable-pin"]
    assert {r.path for r in pin_rows} == {"dags/etl_daily.py", "dags/partitions.py"}  # modern.py has schedule=None
    for r in pin_rows + [r for r in result.rows if r.rule_id in ("airflow3-templates", "airflow3-template-judgment")]:
        assert re.search(r"render-checked: (all \d+ template field\(s\) of this DAG )?identical across \d+ runs \(incl\. 23:00",
                         r.note), (r.path, r.line, r.note)
    for r in result.rows:
        if r.rule_id in ("airflow3-context-keys", "airflow3-context-judgment") and r.path.endswith(".py"):
            if 'context["' in r.after or "kwargs" in r.after or 'ctx["' in r.after:
                assert "not render-checked" in r.note
    assert result.manual == []
    assert result.tests_after.passed
    body = result.body
    para = body.split("### Timetable pin", 1)[1].split("\n### ", 1)[0]
    for needed in ("CronTriggerTimetable", "CronDataIntervalTimetable", "create_cron_data_intervals = True",
                   "AIRFLOW__SCHEDULER__CREATE_CRON_DATA_INTERVALS", "deliberately"):
        assert needed in para, needed
    assert "### Render check" in body


@needs_both
def test_agent_without_pin_reverts_mismatching_templates(tmp_path):
    repo, result = agent_run(tmp_path, "nopin", pin=False, model=None)
    assert not [r for r in result.rows if r.rule_id == "airflow3-templates"]  # all reverted
    reverted = [m for m in result.manual if m.rule_id == "airflow3-templates"]
    assert {m.path for m in reverted} >= {"dags/etl_daily.py", "dags/scripts/report.sh", "dags/sql/summary.sql"}
    assert all("render mismatch" in m.reason and "UTC" in m.reason for m in reverted)
    assert "{{ execution_date }}" in (repo / "dags" / "etl_daily.py").read_text()  # back to the original text


@needs_both
def test_agent_retries_claude_with_the_mismatch(tmp_path):
    model = Replay(RUN2, RUN1)  # unpinned: run 2 renders the wrong date at 23:00, run 1 is right
    repo, result = agent_run(tmp_path, "retry", pin=False, model=model)
    template_reqs = [r for r in model.requests if r.rule.id == "airflow3-template-judgment"]
    assert len(template_reqs) == 2
    assert "render mismatch" in template_reqs[1].feedback and "23:00" in template_reqs[1].feedback
    assert RUN1 in (repo / "dags" / "partitions.py").read_text()
    row = next(r for r in result.rows if r.rule_id == "airflow3-template-judgment")
    assert "render-checked: identical across" in row.note


@needs_both
def test_sets_render_in_the_same_order_on_both_versions(tmp_path):
    # real repo: op_kwargs={"pagenames": {"Microsoft", "Apple", ...}} printed in a per-process order
    repo = tmp_path / "r"
    (repo / "dags").mkdir(parents=True)
    (repo / "dags" / "s.py").write_text(
        "import datetime\nfrom airflow import DAG\nfrom airflow.operators.python import PythonOperator\n"
        "with DAG('s', schedule=None, start_date=datetime.datetime(2024, 1, 1)):\n"
        "    PythonOperator(task_id='t', python_callable=print,\n"
        "                   op_kwargs={'names': {'Microsoft', 'Apple', 'Google', 'Facebook', 'Amazon', 'Netflix'}})\n")
    old = render_check.render(Path(AF2) / "bin" / "python", repo, ["dags/s.py"])
    new = render_check.render(Path(AF3) / "bin" / "python", repo, ["dags/s.py"])
    for _ in range(2):  # a second render must agree too
        assert render_check.render(Path(AF2) / "bin" / "python", repo, ["dags/s.py"])["dags"] == old["dags"]
    old_kw = old["dags"]["dags/s.py::s"]["tasks"]["t"]["fields"]["op_kwargs"]["raw"]
    new_kw = new["dags"]["dags/s.py::s"]["tasks"]["t"]["fields"]["op_kwargs"]["raw"]
    assert old_kw == new_kw


@needs_both
def test_dag_folders_with_same_named_packages_render_in_one_call(tmp_path):
    # real repo (BasPH): each chapter has its own dags/custom package; rendered in one process,
    # the first chapter's `custom` shadowed the others ("No module named 'custom.hooks'")
    repo = tmp_path / "r"
    for chapter, value in (("ch1", "one"), ("ch2", "two")):
        pkg = repo / chapter / "dags" / "custom"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        (pkg / f"only_{chapter}.py").write_text(f"VALUE = {value!r}\n")
        (repo / chapter / "dags" / "d.py").write_text(
            f"import datetime\nfrom custom.only_{chapter} import VALUE\nfrom airflow import DAG\n"
            "from airflow.operators.bash import BashOperator\n"
            f"with DAG('{chapter}', schedule='@daily', start_date=datetime.datetime(2024, 1, 1)):\n"
            "    BashOperator(task_id='t', bash_command='echo ' + VALUE + ' {{ ds }}')\n")
    files = ["ch1/dags/d.py", "ch2/dags/d.py"]
    for venv in (AF2, AF3):
        out = render_check.render(Path(venv) / "bin" / "python", repo, files)
        assert out["errors"] == {}, out["errors"]
        raws = {k: d["tasks"]["t"]["fields"]["bash_command"]["raw"] for k, d in out["dags"].items()}
        assert raws == {"ch1/dags/d.py::ch1": "echo one {{ ds }}", "ch2/dags/d.py::ch2": "echo two {{ ds }}"}


def test_dags_whose_runs_never_pair_are_not_counted_as_identical():
    # real repo (mozilla): Airflow 2 sampled one year, Airflow 3 another, so no run paired and every
    # field was silently dropped, which read as "identical"
    old = side("2", [("a", "2024-01-15T23:00:00+00:00")], {"cmd": {"raw": '"x"', "rendered": ["A"]}})
    new = side("3", [("a", "2020-01-15T23:00:00+00:00")], {"cmd": {"raw": '"x"', "rendered": ["A"]}})
    (res,) = render_check.compare(old, new, changed=set(), whole_dags={"d.py::d"})
    assert res.runs == 0 and res.unpaired
    assert render_check.summary(res).startswith("not render-checked: no run fires at the same moment")


@needs_both
def test_start_date_from_default_args_samples_the_same_runs_on_both_versions(tmp_path):
    repo = tmp_path / "r"
    (repo / "dags").mkdir(parents=True)
    (repo / "dags" / "d.py").write_text(
        "import datetime\nfrom airflow import DAG\nfrom airflow.operators.bash import BashOperator\n"
        "with DAG('d', schedule='0 1 * * *', default_args={'start_date': datetime.datetime(2019, 7, 25)}):\n"
        "    BashOperator(task_id='t', bash_command='echo {{ ds }}')\n")
    old = render_check.render(Path(AF2) / "bin" / "python", repo, ["dags/d.py"])
    new = render_check.render(Path(AF3) / "bin" / "python", repo, ["dags/d.py"])
    fires = [{r["fires"] for r in out["dags"]["dags/d.py::d"]["runs"]} for out in (old, new)]
    assert fires[0] and fires[0] == fires[1]


def test_runs_on_only_one_side_are_mismatches_not_skipped():
    # mutation test: a pin in the wrong zone fires at the same moments in winter and an hour off in summer;
    # the summer runs never paired and were dropped, so the field read as "identical"
    old = side("2", [("w", "2024-01-15T00:00:00+00:00"), ("s", "2024-07-15T00:00:00+00:00")],
               {"cmd": {"raw": '"x"', "rendered": ["A", "B"]}})
    new = side("3", [("w", "2024-01-15T00:00:00+00:00"), ("s", "2024-07-14T23:00:00+00:00")],
               {"cmd": {"raw": '"x"', "rendered": ["A", "B"]}})
    (res,) = render_check.compare(old, new, changed=set(), whole_dags={"d.py::d"})
    assert res.runs == 3 and len(res.mismatches) == 2
    assert {m.new for m in res.mismatches} >= {"(no run fires at this moment on Airflow 3)"}
    assert render_check.summary(res).startswith("render mismatch on 2 of 3 runs")
