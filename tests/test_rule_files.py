"""Custom rules: one self-contained .py file per rule (rule_files.py), loaded from .airflow-verifier/rules/."""
import shutil
from pathlib import Path

import pytest

from api_change_agent.agent import Options, run
from api_change_agent.rule_files import load_rule_file, with_rule_files
from api_change_agent.rules import RuleError, load_rules
from conftest import FIXTURES, ROOT

AIRFLOW_RULES = ROOT / "rules" / "airflow.yaml"
EXAMPLES = ROOT / "docs" / "example-rules"

CATCHUP = '''
import re
from pathlib import Path

from api_change_agent.rule_files import Site

ID = "acme-catchup-true"
DESCRIPTION = "Our scheduler runs with catchup on; DAGs say so explicitly."
DOCS = "https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dag-run.html#catchup"
FIX = "deterministic"
NOTE = "team convention"


def find(path: Path, text: str):
    return [Site(text.count("\\n", 0, m.start()) + 1, m.group(0)) for m in re.finditer(r"catchup=False", text)]


def apply(path: Path, text: str) -> str:
    return text.replace("catchup=False", "catchup=True")
'''


def write_rule(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text)
    return folder / name


@pytest.mark.parametrize("text, error", [
    ("ID = 'x'\n", "needs `DESCRIPTION`"),
    ("ID = 'Bad ID'\nDESCRIPTION = DOCS = FIX = ''\ndef find(p, t): return []\n", "ID must be"),
    ("ID = 'x'\nDESCRIPTION = ''\nDOCS = 'https://x'\nFIX = 'llm'\ndef find(p, t): return []\n", "FIX must be"),
    ("ID = 'x'\nDESCRIPTION = ''\nDOCS = 'https://x'\nFIX = 'deterministic'\ndef find(p, t): return []\n",
     "needs an apply"),
    ("raise ValueError('boom')\n", "failed to import: ValueError: boom"),
])
def test_a_malformed_rule_file_says_what_is_wrong(tmp_path, text, error):
    with pytest.raises(RuleError, match=error):
        load_rule_file(write_rule(tmp_path, "r.py", text))


def test_rule_files_join_the_ruleset_and_broken_ones_are_reported(tmp_path):
    folder = tmp_path / "rules"
    shutil.copy(EXAMPLES / "require_dag_owner.py", folder.mkdir() or folder / "require_dag_owner.py")
    write_rule(folder, "test_require_dag_owner.py", "raise RuntimeError('tests are not rules')\n")
    write_rule(folder, "_helpers.py", "raise RuntimeError('helpers are not rules')\n")
    write_rule(folder, "broken.py", "ID = 'broken'\n")
    write_rule(folder, "clash.py", "ID = 'airflow3-days-ago'\nDESCRIPTION = ''\nDOCS = 'https://x'\nFIX = 'manual'\n"
                                    "def find(p, t): return []\n")
    ruleset, notes = with_rule_files(load_rules(AIRFLOW_RULES), folder, log=lambda *_: None)
    added = [r for r in ruleset.rules if r.match.plugin.startswith("file:")]
    assert [(r.id, r.fix) for r in added] == [("require-dag-owner", "manual")]
    assert len(notes) == 2 and "broken.py: needs `DESCRIPTION`" in notes[0] and "already used" in notes[1]
    assert with_rule_files(load_rules(AIRFLOW_RULES), tmp_path / "missing")[1] == []


def test_migrate_mode_applies_a_deterministic_rule_file(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "airflow_repo", repo)
    write_rule(repo / ".airflow-verifier" / "rules", "catchup.py", CATCHUP)
    result = run(Options(repo=repo, rules_path=AIRFLOW_RULES, dry_run=True, test_command=None, log=lambda *_: None),
                 model=None)
    rows = [r for r in result.rows if r.rule_id == "acme-catchup-true"]
    assert rows and all("catchup=True" in r.after and "catchup=False" in r.before for r in rows)
    assert "catchup=False" not in (repo / "dags" / "etl_daily.py").read_text()
    assert "acme-catchup-true" in result.body


def test_migrate_mode_lists_a_manual_rule_file_and_custom_rules_can_be_turned_off(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "airflow_repo", repo)
    shutil.copy(EXAMPLES / "require_dag_owner.py", (repo / ".airflow-verifier" / "rules").mkdir(parents=True)
                or repo / ".airflow-verifier" / "rules" / "owner.py")
    opts = dict(rules_path=AIRFLOW_RULES, dry_run=True, test_command=None, log=lambda *_: None)
    result = run(Options(repo=repo, **opts), model=None)
    assert any(m.rule_id == "require-dag-owner" and m.path == "dags/etl_daily.py" for m in result.manual)
    off = run(Options(repo=repo, custom_rules=None, **opts), model=None)
    assert not any(m.rule_id == "require-dag-owner" for m in off.manual)


def test_verify_reads_custom_rules_from_the_before_tree_only(tmp_path, monkeypatch):
    # the after tree is the pull request: rules it adds must not run inside the action
    from api_change_agent import verify as v
    from api_change_agent.testrun import TestOutcome
    before, after = tmp_path / "before", tmp_path / "after"
    for tree in (before, after):
        shutil.copytree(FIXTURES / "airflow_repo", tree)
    write_rule(after / ".airflow-verifier" / "rules", "catchup.py", CATCHUP)
    ok = TestOutcome("dag check", True, 0, has_junit=True)
    monkeypatch.setattr(v, "_dag_check", lambda *a, **k: ok)
    kw = dict(rules_path=AIRFLOW_RULES, venv=tmp_path / "none", old_venv=None, log=lambda *_: None)
    res, _ = v.verify(before, after, **kw)
    assert not any(m.rule_id == "acme-catchup-true" for m in res.manual)
    write_rule(before / ".airflow-verifier" / "rules", "catchup.py", CATCHUP)
    res, _ = v.verify(before, after, **kw)
    found = [m for m in res.manual if m.rule_id == "acme-catchup-true"]
    assert found and found[0].reason.endswith("Migrate mode fixes it.")


def test_sponsor_line_only_in_the_job_summary_of_a_run_that_found_something(tmp_path, monkeypatch):
    from api_change_agent import cli, verify as v
    from api_change_agent.report import SPONSOR_LINE
    from api_change_agent.testrun import TestOutcome
    ok = TestOutcome("dag check", True, 0, has_junit=True)
    monkeypatch.setattr(v, "_dag_check", lambda *a, **k: ok)
    clean = tmp_path / "clean"
    (clean / "dags").mkdir(parents=True)
    (clean / "dags" / "a.py").write_text("from airflow.sdk import DAG\n\nDAG('a', schedule=None)\n")

    def summary(tree, *extra):
        step = tmp_path / f"summary-{len(list(tmp_path.glob('summary-*')))}.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step))
        v.main(["--before", str(tree), "--after", str(tree), "--venv", str(tmp_path / "none"), *extra])
        return step.read_text()

    assert SPONSOR_LINE in summary(ROOT / "examples")
    assert SPONSOR_LINE not in summary(ROOT / "examples", "--no-sponsor-link")
    assert SPONSOR_LINE not in summary(clean)
    # the Action input reaches the flag
    seen = {}
    monkeypatch.setattr(v, "main", lambda argv: seen.setdefault("argv", argv) and 0)
    for k, val in {"MODE": "verify", "BEFORE": "a", "AFTER": "b", "SHOW-SPONSOR-LINK": "false"}.items():
        monkeypatch.setenv(f"INPUT_{k}", val)
    monkeypatch.setenv("INPUT_CUSTOM-RULES", "none")
    cli.main([])
    assert "--no-sponsor-link" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--custom-rules") + 1] == ""
