"""--dry-run --output writes the PR body, the diff and a JSON summary; nothing else leaves the machine."""
import json
import subprocess

from api_change_agent.cli import main
from api_change_agent.testrun import TestRunner
from conftest import RULES


def test_dry_run_output_files(sample_repo, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    out = tmp_path / "out"
    assert main(["--repo", str(sample_repo), "--rules", str(RULES), "--dry-run",
                 "--test-command", "none", "--output", str(out)]) == 0
    body = (out / "pr_body.md").read_text()
    diff = (out / "changes.diff").read_text()
    data = json.loads((out / "result.json").read_text())
    assert "| Location | Before → After | Rule | Changelog |" in body
    assert "--- a/app/structured.py" in diff and "+++ b/app/structured.py" in diff
    assert "-openai==1.50.0" in diff and "+openai==1.92.0" in diff
    assert data["rules_fired"] == ["beta-chat-completions-to-ga"]
    assert data["changed_files"] == ["app/structured.py", "requirements.txt"]
    assert [m["rule_id"] for m in data["manual"]] == ["functions-to-tools"]
    assert data["tests_before"] is None


def test_secret_like_env_never_reaches_repo_code(tmp_path, monkeypatch):
    for name in ("OPENAI_API_KEY", "CLAUDE_CODE_MESSAGING_TOKEN", "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("HARMLESS", "1")
    env = TestRunner(tmp_path, "true")._env()
    assert not {"CLAUDE_CODE_MESSAGING_TOKEN", "AWS_SECRET_ACCESS_KEY", "DB_PASSWORD"} & set(env)
    assert env["OPENAI_API_KEY"] != "x"  # replaced by the dummy key
    assert env["HARMLESS"] == "1" and "PATH" in env


def test_venv_python_used(tmp_path):
    import sys
    r = TestRunner(tmp_path, "python -c 'import sys; print(sys.prefix)'", venv_dir=tmp_path / "v",
                   venv_python=sys.executable)
    r.setup()
    out = subprocess.run(["bash", "-c", r.command], env=r._env(), capture_output=True, text=True).stdout
    assert str(tmp_path / "v") in out


def test_dummy_openai_key_replaces_real_one(tmp_path, monkeypatch):
    from api_change_agent.testrun import DUMMY_OPENAI_KEY
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    assert TestRunner(tmp_path, "true")._env()["OPENAI_API_KEY"] == DUMMY_OPENAI_KEY


def test_install_command_includes_dev_dependencies(tmp_path):
    from api_change_agent.testrun import default_install_command
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[project.optional-dependencies]\ntest = ["pytest-asyncio"]\ndocs = ["sphinx"]\n'
        '[dependency-groups]\ndev = ["ruff"]\n'
        '[tool.poetry.group.dev.dependencies]\nvcrpy = "^6.0"\npytest-mock = {version = "^3.1"}\nlocal = {path = "../x"}\n'
        '[tool.poetry.dev-dependencies]\nfreezegun = "*"\n'
    )
    cmd = default_install_command(tmp_path, "python -m pytest -q")
    assert "pip install -q -e '.[test]'" in cmd and "docs" not in cmd
    assert "--group dev" in cmd
    assert "'vcrpy>=6.0,<7'" in cmd and "'pytest-mock>=3.1,<4'" in cmd and " freezegun" in cmd
    assert "local" not in cmd


def test_collection_errors_do_not_stop_the_suite(tmp_path):
    import sys
    (tmp_path / "test_ok.py").write_text("def test_a():\n    pass\n\ndef test_b():\n    assert False\n")
    (tmp_path / "test_broken.py").write_text("import not_a_module_anywhere\n")
    out = TestRunner(tmp_path, f"{sys.executable} -m pytest -q -p no:cacheprovider").run()
    assert out.executed == 2  # test_ok ran despite test_broken failing to import
    assert out.failures == {"::test_broken", "test_ok::test_b"}
    assert out.summary() == "1 passed, 1 failing, 1 collection error"
