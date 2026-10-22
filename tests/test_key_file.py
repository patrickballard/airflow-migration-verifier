"""The CLI's Anthropic key: env var or ~/.config/api-change-agent/anthropic_key, never leaked.
Also: the Claude client's hard USD budget."""
import json
import os
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from api_change_agent import cli
from api_change_agent.llm import BudgetExhausted, ClaudePatchModel, PatchError, PatchRequest, cost
from api_change_agent.rules import load_rules
from conftest import RULES

FAKE = "sk-ant-api03-FAKE-test-key-0123456789abcdef"


class FakeAnthropic:
    seen = []

    def __init__(self, api_key=None, **_):
        FakeAnthropic.seen.append(api_key)


class OfflineModel:
    """Stands in for ClaudePatchModel; records how it was built, never calls out."""
    built = []

    def __init__(self, client=None, model=None, max_usd=None):
        OfflineModel.built.append({"client": client, "model": model, "max_usd": max_usd})
        self.last_notes = ""

    def propose(self, request):
        raise PatchError("offline test model")


@pytest.fixture
def offline(monkeypatch):
    import anthropic

    FakeAnthropic.seen.clear()
    OfflineModel.built.clear()
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    monkeypatch.setattr(cli, "ClaudePatchModel", OfflineModel)


def write_key(text=FAKE, mode=0o600):
    path = cli.key_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")
    path.chmod(mode)
    return path


def run_cli(repo, out, *extra):
    return cli.main(["--repo", str(repo), "--rules", str(RULES), "--dry-run", "--output", str(out), *extra])


def test_key_file_is_used_and_never_leaks(sample_repo, tmp_path, offline, capsys):
    write_key()
    # a "test suite" that records the environment repo code gets
    dump = f"{shlex.quote(sys.executable)} -c \"import json, os; json.dump(dict(os.environ), open('env.json', 'w'))\""
    out = tmp_path / "out"
    assert run_cli(sample_repo, out, "--test-command", dump, "--max-llm-usd", "0.5") == 0

    assert FakeAnthropic.seen == [FAKE]  # handed to the client, stripped of the newline
    assert OfflineModel.built[0]["max_usd"] == 0.5
    printed = capsys.readouterr()
    assert FAKE not in printed.out and FAKE not in printed.err
    for f in out.iterdir():
        assert FAKE not in f.read_text(), f.name
    assert FAKE not in json.dumps(dict(os.environ)) and "ANTHROPIC_API_KEY" not in os.environ
    env_seen_by_tests = json.loads((sample_repo / "env.json").read_text())
    assert FAKE not in json.dumps(env_seen_by_tests)
    assert "ANTHROPIC_API_KEY" not in env_seen_by_tests
    # the Claude rule was attempted (with the offline model), so the key really was picked up
    manual = json.loads((out / "result.json").read_text())["manual"]
    assert any("offline test model" in m["reason"] for m in manual)


def test_env_var_wins_over_file(offline, monkeypatch):
    write_key("sk-ant-from-file")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    assert cli.anthropic_api_key() == "sk-ant-from-env"


@pytest.mark.parametrize("content", [None, "", "   "])
def test_no_key_means_no_model(content, sample_repo, tmp_path, offline):
    if content is not None:
        write_key(content)
    assert cli.anthropic_api_key() is None
    assert run_cli(sample_repo, tmp_path / "out", "--test-command", "none") == 0
    assert OfflineModel.built == [] and FakeAnthropic.seen == []
    manual = json.loads((tmp_path / "out" / "result.json").read_text())["manual"]
    assert any("no ANTHROPIC_API_KEY" in m["reason"] for m in manual)


def test_readable_key_file_warns_without_printing_it(offline, capsys):
    path = write_key(mode=0o644)
    assert cli.anthropic_api_key() == FAKE
    err = capsys.readouterr().err
    assert "chmod 600" in err and str(path) in err and FAKE not in err


# --- budget --------------------------------------------------------------------------

class FakeStream:
    def __init__(self, calls, message):
        self.calls, self.message = calls, message

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


def fake_client(output_tokens=1000, stop_reason="tool_use", source="x = 1\n"):
    calls = []
    usage = NS(input_tokens=2000, output_tokens=output_tokens, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    content = [NS(type="tool_use", name="submit_patch", input={"updated_source": source, "notes": "ok"})]
    msg = NS(model="claude-opus-5-5", usage=usage, stop_reason=stop_reason, content=content, stop_details=None)
    stream = FakeStream(calls, msg)
    return NS(messages=NS(stream=stream), beta=NS(messages=NS(stream=stream))), calls


def request():
    rule = load_rules(RULES).rules[1]
    return PatchRequest("a.py", "import openai\n", rule, [])


def test_budget_clamps_max_tokens_and_stops():
    client, calls = fake_client(output_tokens=1000)
    m = ClaudePatchModel(client=client, max_usd=0.10)
    m.propose(request())
    first = calls[0]
    assert first["fallbacks"] == "default" and first["betas"] == ["server-side-fallback-2026-07-01"]
    assert first["tool_choice"] == {"type": "auto"}
    assert first["max_tokens"] < 5000  # $0.10 can't pay for 32k output tokens at $20/M
    assert m.spent_usd == pytest.approx(cost("claude-opus-5-5", 2000, 1000))
    m.calls[0].usd = 0.099  # pretend the budget is nearly gone
    with pytest.raises(BudgetExhausted, match=r"\$0.10 used up"):
        m.propose(request())
    assert len(calls) == 1  # the second call was never made


def test_refusal_is_reported_as_declined():
    client, _ = fake_client(stop_reason="refusal")
    with pytest.raises(PatchError, match="declined"):
        ClaudePatchModel(client=client, max_usd=1).propose(request())


def test_unknown_model_priced_at_the_top_rate():
    assert cost("some-future-model", 0, 1_000_000) == cost("claude-fable-5-1", 0, 1_000_000)
