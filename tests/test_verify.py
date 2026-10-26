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
    assert "execution_date" in report.split("### Airflow 2 code that breaks on Airflow 3", 1)[1]
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


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   check=True, capture_output=True)


def test_one_checkout_verifies_head_against_its_parent(tmp_path, monkeypatch):
    # the short Quickstart: actions/checkout with fetch-depth 2, no before/after inputs
    from api_change_agent import cli, verify
    repo = tmp_path / "workspace"
    write(repo, "dags/a.py", "from airflow.utils.dates import days_ago\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    write(repo, "dags/a.py", "import pendulum\n")
    _git(repo, "commit", "-qam", "migrate")
    seen = {}
    monkeypatch.setattr(verify, "main", lambda argv: seen.setdefault("argv", argv) and 0)
    monkeypatch.setenv("INPUT_MODE", "verify")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(repo))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "tmp"))
    assert cli.main([]) == 0
    argv = seen["argv"]
    before, after = Path(argv[argv.index("--before") + 1]), Path(argv[argv.index("--after") + 1])
    assert after == repo
    assert (before / "dags/a.py").read_text() == "from airflow.utils.dates import days_ago\n"
    assert not (before / ".git").exists() and (repo / "dags/a.py").read_text() == "import pendulum\n"


def test_one_checkout_without_a_parent_says_to_fetch_two_commits(tmp_path, monkeypatch, capsys):
    from api_change_agent import cli
    repo = tmp_path / "workspace"
    write(repo, "dags/a.py", "x = 1\n")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "only")
    monkeypatch.setenv("INPUT_MODE", "verify")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(repo))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "tmp"))
    assert cli.main([]) == 2
    assert "fetch-depth: 2" in capsys.readouterr().out


def test_removed_openai_ruleset_names_the_last_version(capsys):
    from api_change_agent import cli
    assert cli.main(["--rules", "openai", "--dry-run"]) == 2
    assert "@v1.3.3" in capsys.readouterr().out


def test_verify_lists_every_removed_api_at_once(tmp_path, monkeypatch):
    # an import error names only the first removed API; ruff's AIR3 rules and our own name all of them
    from api_change_agent import verify as v
    from api_change_agent.testrun import TestOutcome
    shutil.copy(ROOT / "examples" / "broken_dag.py", tmp_path / "broken_dag.py")
    err = "ModuleNotFoundError: No module named 'airflow.operators.bash_operator'"
    outcome = TestOutcome("dag check", False, 1, frozenset({"broken_dag.py::import"}), True,
                          ids=("broken_dag.py::import",), messages={"broken_dag.py::import": err})
    monkeypatch.setattr(v, "_dag_check", lambda *a, **k: outcome)
    res, _ = v.verify(tmp_path, tmp_path, rules_path=AIRFLOW_RULES, venv=tmp_path / "none", old_venv=None,
                      log=lambda *_: None)
    found = {(m.line, m.rule_id) for m in res.manual}
    assert {(17, "airflow3-context-keys"), (23, "ruff AIR301"), (24, "ruff AIR301"), (28, "ruff AIR302"),
            (31, "airflow3-templates"), (33, "ruff AIR302"), (36, "ruff AIR301")} <= found
    assert not any(m.rule_id == "ruff AIR311" for m in res.manual)  # still works on Airflow 3
    days_ago = next(m for m in res.manual if m.line == 23)
    assert days_ago.reason.endswith("Migrate mode fixes it.")  # our rule, not ruff's
    assert next(m for m in res.manual if m.line == 36).reason.endswith("Migrate mode fixes it.")
    assert "❌ **9 Airflow 3 problem(s) found in 1 file(s).**" in res.body
    assert "Checked `" in res.body and "as it is" in res.body


def test_clean_verify_says_so():
    from api_change_agent.report import verify_headline
    assert verify_headline([], [], [], []) == "✅ **No Airflow 3 problems found.**"


def test_a_file_that_fails_on_airflow_2_too_is_not_an_airflow_3_problem(tmp_path, monkeypatch):
    # found on real repos: a missing Variable, connection or package fails the import on both versions
    from api_change_agent import verify as v
    from api_change_agent.testrun import TestOutcome
    write(tmp_path, "dags/needs_var.py", "from airflow.sdk import DAG, Variable\nDAG(Variable.get('X'), schedule=None)\n")
    err = "KeyError: 'Variable X does not exist.'"
    failed = TestOutcome("dag check", False, 1, frozenset({"dags/needs_var.py::import"}), True,
                         ids=("dags/needs_var.py::import",), messages={"dags/needs_var.py::import": err})
    monkeypatch.setattr(v, "_dag_check", lambda *a, **k: failed)
    res, dags = v.verify(tmp_path, tmp_path, rules_path=AIRFLOW_RULES, venv=tmp_path / "af3",
                         old_venv=tmp_path / "af2", log=lambda *_: None)
    assert res.unverified == []
    assert any("doesn't import on Airflow 2 either" in s and "Variable X" in s for s in res.skipped)
    assert "✅ **No Airflow 3 problems found.**" in res.body
    summary = res.summary()
    summary["dags"] = dags
    assert v.strict_problems(summary) == []


def test_import_paths_airflow_3_only_deprecated_are_not_counted(tmp_path, monkeypatch):
    # ruff's AIR302 calls airflow.operators.email.EmailOperator moved; on Airflow 3.3.2 it still imports
    from api_change_agent import verify as v
    from api_change_agent.testrun import TestOutcome
    write(tmp_path, "dags/d.py", "from airflow.operators.email import EmailOperator\n"
                                 "from airflow.operators.dummy import DummyOperator\n"
                                 "EmailOperator(task_id='e', to='a@b.c', subject='s', html_content='h')\n"
                                 "DummyOperator(task_id='d')\n")
    ok = TestOutcome("dag check", True, 0, has_junit=True)
    monkeypatch.setattr(v, "_dag_check", lambda *a, **k: ok)
    seen = {}
    monkeypatch.setattr(v, "still_imports", lambda python, names: seen.setdefault("names", names) and {
        n: n.endswith("EmailOperator") for n in names})
    res, _ = v.verify(tmp_path, tmp_path, rules_path=AIRFLOW_RULES, venv=tmp_path / "none", old_venv=None,
                      log=lambda *_: None)
    flagged = {m.reason.split("`")[1] for m in res.manual if m.rule_id.startswith("ruff")}
    assert "airflow.operators.dummy.DummyOperator" in flagged
    assert "airflow.operators.email.EmailOperator" not in flagged
    assert any("still import" in s and "EmailOperator" in s for s in res.skipped)


@pytest.mark.skipif(not os.environ.get("ACA_AIRFLOW3_VENV"), reason="set ACA_AIRFLOW3_VENV")
def test_still_imports_on_real_airflow_3():
    from api_change_agent.verify import still_imports
    python = Path(os.environ["ACA_AIRFLOW3_VENV"]) / "bin" / "python"
    assert still_imports(python, ["airflow.operators.email.EmailOperator", "airflow.utils.dates.days_ago",
                                  "airflow.operators.dummy.DummyOperator"]) == {
        "airflow.operators.email.EmailOperator": True, "airflow.utils.dates.days_ago": False,
        "airflow.operators.dummy.DummyOperator": False}


@pytest.mark.parametrize("airflow2, airflow3, env_only", [
    # the same failure on both: the environment lacks something
    ("KeyError: 'Variable X does not exist'", "KeyError: 'Variable X does not exist.'", True),
    ("airflow.exceptions.AirflowNotFoundException: The conn_id `c` isn't defined",
     "airflow.sdk.exceptions.AirflowNotFoundException: The conn_id `c` isn't defined", True),
    # a library the environment can't run, in different places on each version
    ("ImportError: cannot import name 'A' from 'kafka.errors' (/v/af2/kafka/errors.py)",
     "ImportError: cannot import name 'B' from 'kafka.util' (/v/real-world-airflow/kafka/util.py)", True),
    # Airflow 3 fails earlier, on something Airflow 3 changed: still an Airflow 3 problem
    ("KeyError: 'Variable X does not exist'", "TypeError: DAG.__init__() got an unexpected keyword argument 'x'", False),
    ("KeyError: 'Variable X does not exist'", "ModuleNotFoundError: No module named 'airflow.datasets'", False),
    ("AirflowDagCycleException: Cycle detected in DAG: c", "ModuleNotFoundError: No module named 'airflow.operators.dummy'",
     False),
])
def test_which_failures_on_both_versions_are_the_environment(airflow2, airflow3, env_only):
    from api_change_agent.verify import environment_only
    assert environment_only(airflow2, airflow3) is env_only
