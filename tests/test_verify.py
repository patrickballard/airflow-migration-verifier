"""Verify-only mode: check an Airflow 2 tree against an Airflow 3 tree migrated by anything.

DAG import on Airflow 3, the removed-method and kwarg checks, and the render check by fire time,
respecting what the "after" tree configures (e.g. [scheduler] create_cron_data_intervals = True,
which legitimately restores Airflow 2's run dates for every unpinned cron DAG).
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from api_change_agent.agent import Options, run
from api_change_agent.airflow_config import find_setting
from api_change_agent.cli import main
from conftest import FIXTURES, ROOT

AF2, AF3 = os.environ.get("ACA_AIRFLOW2_VENV"), os.environ.get("ACA_AIRFLOW3_VENV")
needs_both = pytest.mark.skipif(not (AF2 and AF3), reason="set ACA_AIRFLOW2_VENV and ACA_AIRFLOW3_VENV")
AIRFLOW_RULES = ROOT / "rules" / "airflow.yaml"
CRON = ("scheduler", "create_cron_data_intervals")


def write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)


@pytest.mark.parametrize("rel, text, value", [
    ("airflow.cfg", "[core]\nx = 1\n[scheduler]\ncreate_cron_data_intervals = True\n", "True"),
    (".env", "AIRFLOW__SCHEDULER__CREATE_CRON_DATA_INTERVALS=true\n", "true"),
    ("docker-compose.yaml", "x:\n  environment:\n    AIRFLOW__SCHEDULER__CREATE_CRON_DATA_INTERVALS: 'True'\n", "True"),
    ("Dockerfile", "ENV AIRFLOW__SCHEDULER__CREATE_CRON_DATA_INTERVALS=True\n", "True"),
    ("chart/values.yaml", "config:\n  scheduler:\n    create_cron_data_intervals: true\n", "true"),
])
def test_finds_scheduler_settings_the_after_tree_sets(tmp_path, rel, text, value):
    write(tmp_path, rel, text)
    found = find_setting(tmp_path, *CRON)
    assert found.value.lower() == value.lower() and found.is_true() and found.sources[0].path == rel


def test_unset_setting(tmp_path):
    write(tmp_path, "airflow.cfg", "[core]\ncreate_cron_data_intervals = True\n")  # wrong section
    assert find_setting(tmp_path, *CRON).value is None


def test_verify_needs_both_trees(capsys):
    with pytest.raises(SystemExit):
        main(["verify", "--before", "x"])
    assert "--after" in capsys.readouterr().err


# --- against real Airflow -----------------------------------------------------------------------

def migrated_by_us(tmp_path) -> Path:
    after = tmp_path / "ours"
    shutil.copytree(FIXTURES / "airflow_repo", after)
    run(Options(repo=after, rules_path=AIRFLOW_RULES, dry_run=True, test_command=None, log=lambda *_: None),
        model=None)
    return after


def unpin(tree: Path) -> None:
    for f in (tree / "dags").glob("*.py"):
        text = re.sub(r'CronDataIntervalTimetable\(("[^"]+"), timezone="UTC"\)', r"\1", f.read_text())
        f.write_text(text.replace("from airflow.sdk import CronDataIntervalTimetable\n", "")
                     .replace(", CronDataIntervalTimetable\n", "\n"))


def verify(tmp_path, after: Path, name: str):
    out = tmp_path / f"out-{name}"
    code = main(["verify", "--before", str(FIXTURES / "airflow_repo"), "--after", str(after), "--rules", "airflow",
                 "--venv", AF3, "--old-venv", AF2, "--output", str(out)])
    import json
    return code, json.loads((out / "result.json").read_text()), (out / "report.md").read_text()


@needs_both
def test_our_output_verifies_clean(tmp_path):
    code, res, report = verify(tmp_path, migrated_by_us(tmp_path), "ours")
    etl = res["dags"]["dags/etl_daily.py::etl_daily"]
    assert etl["imports"] and etl["render"] == "identical", etl
    assert "### ⚠️ Run dates will shift" not in report
    assert "### Render check" in report and "### DAG checks on" in report
    assert "Every row links to the changelog" not in report  # verify rows come from a diff, not from rules


@needs_both
def test_unpinned_cron_dags_are_caught(tmp_path):
    after = migrated_by_us(tmp_path)
    unpin(after)
    code, res, report = verify(tmp_path, after, "unpinned")
    etl = res["dags"]["dags/etl_daily.py::etl_daily"]
    assert etl["imports"] and etl["render"] == "mismatch", etl
    assert "### ⚠️ Run dates will shift" in report and "dags/etl_daily.py" in report
    # on Airflow 3's trigger timetables data_interval_start == data_interval_end (checked on 3.3.2)
    assert "a task that processes the rows between them processes none" in report


@needs_both
def test_a_tree_that_sets_the_scheduler_flag_is_respected(tmp_path):
    after = migrated_by_us(tmp_path)
    unpin(after)
    write(after, "airflow.cfg", "[scheduler]\ncreate_cron_data_intervals = True\n")  # the global alternative
    code, res, report = verify(tmp_path, after, "flag")
    etl = res["dags"]["dags/etl_daily.py::etl_daily"]
    assert etl["render"] == "identical", etl
    assert "### ⚠️ Run dates will shift" not in report
    assert "create_cron_data_intervals = True" in report  # says which setting it used, and where


@needs_both
def test_removed_methods_and_rejected_kwargs_are_listed(tmp_path):
    after = migrated_by_us(tmp_path)
    p = after / "dags" / "partitions.py"
    p.write_text(p.read_text() + "\n\ndef nxt(dag):\n    return dag.following_schedule(None)\n")
    code, res, report = verify(tmp_path, after, "methods")
    assert any(m["rule_id"] == "airflow3-removed-dag-methods" and m["path"] == "dags/partitions.py"
               for m in res["manual"])
    assert "following_schedule" in report


@needs_both
def test_removed_context_keys_left_in_python_are_listed(tmp_path):
    # imports fine and nothing renders differently, but the task fails at run time on Airflow 3
    after = migrated_by_us(tmp_path)
    p = after / "dags" / "partitions.py"
    p.write_text(p.read_text() + '\n\ndef hour(**context):\n    return context["execution_date"].hour\n')
    code, res, report = verify(tmp_path, after, "context")
    from api_change_agent.verify import strict_problems
    assert any(m["rule_id"] == "airflow3-context-keys" and m["path"] == "dags/partitions.py" for m in res["manual"])
    assert any("execution_date" in p.text and p.path == "dags/partitions.py" and p.line for p in strict_problems(res))
    assert "execution_date" in report.split("### Needs manual migration", 1)[1]
    # says what to write, not just that migrate mode would: the PR may be someone else's
    assert any(m["path"] == "dags/partitions.py" and "write the line as `return context[\"logical_date\"].hour`"
               in m["reason"] for m in res["manual"])


@needs_both
def test_the_report_describes_verify_mode(tmp_path):
    after = tmp_path / "untouched"
    shutil.copytree(FIXTURES / "airflow_repo", after)
    code, res, report = verify(tmp_path, after, "wording")
    assert report.startswith("## Airflow Migration Verifier: verify an Airflow 2 → 3 change")
    assert "external change" not in report and "`verify: " not in report  # no rule/changelog table for a diff
    dag_checks = report.split("### DAG checks on", 1)[1].split("###", 1)[0]
    assert "imported (DagBag)" in dag_checks and "dag.test()" not in dag_checks  # verify runs import-only
    assert "so it's expected to fail there" in dag_checks  # a red Before column is normal, and the report says why
    unverified = report.split("### ⚠️ Unverified files", 1)[1].split("###", 1)[0]
    assert "run the agent again" not in unverified and "Claude" not in unverified
    assert "Migrate mode fixes common causes" in unverified
    assert "No new test failures" not in report  # it failed before and after: say so, don't reassure
    assert "Opened by" not in report  # verify opens nothing


@needs_both
def test_a_file_that_still_fails_is_unverified(tmp_path):
    after = tmp_path / "untouched"
    shutil.copytree(FIXTURES / "airflow_repo", after)  # "migrated" by nothing
    code, res, report = verify(tmp_path, after, "untouched")
    assert not res["dags"]["dags/etl_daily.py::etl_daily"]["imports"]
    assert "dags/etl_daily.py" in {u["path"] for u in res["unverified"]}
    assert "### ⚠️ Unverified files" in report


@needs_both
def test_import_only_checker_runs_no_dag_test():
    out = subprocess.run([str(Path(AF3) / "bin" / "python"), "-I", str(ROOT / "src" / "api_change_agent" / "airflow_dag_check.py"),
                          "--repo", str(FIXTURES / "airflow_repo"), "--import-only"], capture_output=True, text=True,
                         timeout=600).stdout
    ids = [line.split()[1] for line in out.splitlines() if line.startswith(("PASS", "FAIL"))]
    assert ids and all(i.endswith("::import") for i in ids)


# --- the Action's verify mode ---------------------------------------------------------------------

def test_verify_builds_both_airflow_venvs_from_the_trees(tmp_path):
    from api_change_agent.verify import install_commands
    before, after = tmp_path / "before", tmp_path / "after"
    write(before, "requirements.txt", "apache-airflow==2.9.3\npandas\n")
    write(after, "requirements.txt", "apache-airflow==3.3.2\npandas\n")
    from api_change_agent.rules import load_rules
    new, old = install_commands(before, after, load_rules(AIRFLOW_RULES))
    assert "constraints-3.3.2" in new and "apache-airflow==3.3.2" in new and "-r requirements.txt" in new
    assert "constraints-2.9.3" in old and "apache-airflow==2.9.3" in old and "-r requirements.txt" in old
    write(before, "requirements.txt", "pandas\n")  # no pin: the ruleset's source version
    assert "apache-airflow==2.11.2" in install_commands(before, after, load_rules(AIRFLOW_RULES))[1]


def test_action_inputs_select_verify_mode(tmp_path, monkeypatch):
    from api_change_agent import cli, verify
    seen = {}
    monkeypatch.setattr(verify, "main", lambda argv: seen.setdefault("argv", argv) and 0)
    monkeypatch.chdir(tmp_path)
    for k, v in {"MODE": "verify", "BEFORE": "base", "AFTER": "head", "VENV-DIR": "/github/home/aca-venv",
                 "STRICT": "true"}.items():
        monkeypatch.setenv(f"INPUT_{k}", v)
    assert cli.main([]) == 0
    argv = seen["argv"]
    assert argv[argv.index("--before") + 1] == str(tmp_path / "base")
    assert argv[argv.index("--after") + 1] == str(tmp_path / "head")
    assert "--install-command" in argv and argv[argv.index("--install-command") + 1] == "auto"
    assert "--strict" in argv


def test_strict_fails_on_problems_only():
    from api_change_agent.verify import strict_problems
    ok = {"dags": {"a::a": {"imports": True, "render": "identical"}}, "manual": [], "new_failures": []}
    assert strict_problems(ok) == []
    bad = {"dags": {"a::a": {"imports": True, "render": "mismatch"}, "b::b": {"imports": False, "render": "not reached"}},
           "manual": [{"rule_id": "kwarg-check", "path": "a", "line": 1, "reason": "x"}]}
    problems = strict_problems(bad)
    assert len(problems) == 3
    from api_change_agent.verify import annotation
    # GitHub puts each one on the file (and line) it names, in the PR's diff
    assert [annotation(p) for p in problems] == [
        "::error file=b,title=Airflow 3 migration::b::b: does not import on Airflow 3",
        "::error file=a,title=Airflow 3 migration::a::a: renders differently from Airflow 2",
        "::error file=a,line=1,title=Airflow 3 migration::a:1: x"]
    assert annotation(type(problems[0])("d.py", 2, "100%: a\nb")) == (
        "::error file=d.py,line=2,title=Airflow 3 migration::d.py:2: 100%25: a%0Ab")
