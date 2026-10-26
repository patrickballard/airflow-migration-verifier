"""The render check and kwarg check import repo or provider code in their own processes: they must not see
the Anthropic key, the job's GitHub token or any other credential, just like the DAG checks."""
import json
import subprocess
from pathlib import Path

from api_change_agent import kwarg_check, render_check

SECRETS = {"ANTHROPIC_API_KEY": "sk-ant-test", "INPUT_ANTHROPIC-API-KEY": "sk-ant-test", "GITHUB_TOKEN": "ghs_test",
           "INPUT_GITHUB-TOKEN": "ghs_test", "ACTIONS_RUNTIME_TOKEN": "x", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "x"}


def capture(monkeypatch, stdout=""):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw.get("env") or __import__("os").environ)
        if "--out" in cmd:
            Path(cmd[cmd.index("--out") + 1]).write_text(json.dumps({"dags": {}}))
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_render_subprocess_gets_no_credentials(monkeypatch, tmp_path):
    seen = capture(monkeypatch)
    render_check.render(Path("python"), tmp_path, ["d.py"], env={"AIRFLOW__CORE__DEFAULT_TIMEZONE": "UTC"})
    assert not set(SECRETS) & set(seen), sorted(set(SECRETS) & set(seen))
    assert seen["AIRFLOW__CORE__DEFAULT_TIMEZONE"] == "UTC" and seen["PATH"] == "/usr/bin"


def test_kwarg_subprocess_gets_no_credentials(monkeypatch):
    seen = capture(monkeypatch, stdout="{}\n")
    kwarg_check.signatures(Path("python"), ["airflow.providers.x.Op"])
    assert not set(SECRETS) & set(seen), sorted(set(SECRETS) & set(seen))
    assert seen["PATH"] == "/usr/bin"
