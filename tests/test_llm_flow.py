"""Rule 2 end to end with a fake Claude, against the fixture repo's real tests."""
from api_change_agent.agent import Options, run
from conftest import FIXTURE_TEST_COMMAND, FIXTURES, RULES, FileModel

GOOD = FIXTURES / "llm" / "weather_good.py"
BAD = FIXTURES / "llm" / "weather_bad.py"


def opts(repo, **kw):
    return Options(repo=repo, rules_path=RULES, dry_run=True, test_command=FIXTURE_TEST_COMMAND, **kw)


def test_good_patch_kept(sample_repo):
    model = FileModel(GOOD)
    result = run(opts(sample_repo), model=model)
    assert (sample_repo / "app" / "weather.py").read_text() == GOOD.read_text()
    assert len(model.requests) == 1
    req = model.requests[0]
    assert req.path == "app/weather.py"
    assert [s.line for s in req.sites] == [22]
    assert req.feedback is None
    llm_rows = [r for r in result.rows if r.rule_id == "functions-to-tools"]
    assert [r.line for r in llm_rows] == [25, 29]
    assert "functions=[WEATHER_FN]" in llm_rows[0].before
    assert "tools=" in llm_rows[0].after
    assert result.manual == []
    assert result.tests_after.passed
    assert result.new_failures == set()


def test_breaking_patch_retried_then_reverted(sample_repo):
    original = (sample_repo / "app" / "weather.py").read_text()
    model = FileModel(BAD, BAD)
    result = run(opts(sample_repo, max_llm_attempts=2), model=model)
    assert len(model.requests) == 2
    assert "test_ask_weather_runs_the_function_the_model_picked" in model.requests[1].feedback
    assert (sample_repo / "app" / "weather.py").read_text() == original
    assert [(m.path, m.rule_id) for m in result.manual] == [("app/weather.py", "functions-to-tools")]
    assert "tests" in result.manual[0].reason
    assert not [r for r in result.rows if r.rule_id == "functions-to-tools"]
    # the deterministic rule still went through
    assert [r for r in result.rows if r.rule_id == "beta-chat-completions-to-ga"]
    assert result.tests_after.passed


def test_bad_then_good(sample_repo):
    model = FileModel(BAD, GOOD)
    result = run(opts(sample_repo), model=model)
    assert len(model.requests) == 2
    assert (sample_repo / "app" / "weather.py").read_text() == GOOD.read_text()
    assert result.manual == []


def test_unparseable_patch_rejected(sample_repo):
    model = FileModel("def broken(:\n", "still broken(\n")
    result = run(opts(sample_repo), model=model)
    assert "does not parse" in model.requests[1].feedback
    assert result.manual and "does not parse" in result.manual[0].reason


def test_patch_that_leaves_sites_rejected(sample_repo):
    unchanged_kwargs = (sample_repo / "app" / "weather.py").read_text().replace('model="gpt-4o-mini"', 'model="gpt-4o"')
    model = FileModel(unchanged_kwargs)
    result = run(opts(sample_repo, max_llm_attempts=1), model=model)
    assert result.manual and "still" in result.manual[0].reason


def test_no_model_lists_sites_as_manual(sample_repo):
    result = run(opts(sample_repo), model=None)
    assert [(m.path, m.line) for m in result.manual] == [("app/weather.py", 22)]
    assert "ANTHROPIC_API_KEY" in result.manual[0].reason


def test_dry_run_leaves_pins_and_reports(sample_repo):
    result = run(opts(sample_repo), model=FileModel(GOOD))
    req = (sample_repo / "requirements.txt").read_text()
    # dry-run still edits the working tree (that's how tests run); it only skips git/PR.
    assert "openai==1.92.0  # predates the GA parse/stream API" in req
    assert "langchain-openai==0.1.0" in req
    pin_rows = [r for r in result.rows if r.path == "requirements.txt"]
    assert [(r.line, r.before, r.after) for r in pin_rows] == [(2, "openai==1.50.0", "openai==1.92.0")]
    assert "app/no_openai.py" not in {r.path for r in result.rows}
    assert "| Location | Before → After | Rule | Changelog |" in result.body


def test_broken_test_command_disables_gate_and_llm(sample_repo):
    result = run(Options(repo=sample_repo, rules_path=RULES, dry_run=True, test_command="exit 3"),
                 model=FileModel(GOOD))
    assert result.tests_after is None
    assert any("test gate disabled" in s for s in result.skipped)
    assert [(m.path, m.rule_id) for m in result.manual] == [("app/weather.py", "functions-to-tools")]
    assert "untested" in result.body
    assert "No new test failures" not in result.body
    # the deterministic rewrite still lands, flagged as untested
    assert [r for r in result.rows if r.rule_id == "beta-chat-completions-to-ga"]


def test_preexisting_failure_does_not_block(sample_repo):
    (sample_repo / "tests" / "test_flaky.py").write_text("def test_already_broken():\n    assert False\n")
    result = run(opts(sample_repo), model=FileModel(GOOD))
    assert result.manual == []
    assert result.new_failures == set()
    assert not result.tests_after.passed  # still red, but only the old failure
    assert "No new test failures" in result.body


def test_default_install_command(tmp_path):
    from api_change_agent.testrun import default_install_command

    (tmp_path / "requirements.txt").write_text("openai\n")
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    cmd = default_install_command(tmp_path, "python -m pytest -q")
    assert "-r requirements.txt" in cmd and "-e ." not in cmd and "install -q pytest" in cmd
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert "-e ." in default_install_command(tmp_path, None)


def test_claude_notes_kept_in_full(sample_repo):
    long_notes = "Replaced functions with tools. " + "Caveat: " + "x" * 900 + " END-OF-NOTES"

    class NotesModel(FileModel):
        def propose(self, request):
            self.last_notes = long_notes
            return super().propose(request)

    result = run(opts(sample_repo), model=NotesModel(GOOD))
    (entry,) = result.llm_notes
    assert entry["notes"] == long_notes and entry["accepted"] is True
    assert "END-OF-NOTES" in result.body  # the full text reaches the PR
    row = next(r for r in result.rows if r.rule_id == "functions-to-tools")
    assert row.note == "Claude: Replaced functions with tools. (full notes below)"
