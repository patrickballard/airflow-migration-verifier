"""Fixes from the PR #1 review: intentional pins are left alone, and LLM
patches may not drop comments."""
import pytest

from api_change_agent.agent import Options, run
from api_change_agent.codemod import missing_comments
from api_change_agent.pins import apply_pins, plan_pins
from conftest import FIXTURE_TEST_COMMAND, FIXTURES, RULES, SAMPLE_REPO, FileModel

GOOD = FIXTURES / "llm" / "weather_good.py"
NO_COMMENT = FIXTURES / "llm" / "weather_no_comment.py"


def opts(repo, **kw):
    return Options(repo=repo, rules_path=RULES, dry_run=True, test_command=FIXTURE_TEST_COMMAND, **kw)


# --- 1. pin intent ----------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "openai==1.50.0  # pinned below 1.92.0 on purpose\n",
        "openai==1.50.0  # intentional: 1.92 changed retries\n",
        "openai==1.50.0  # keep\n",
        "openai==1.50.0  # don't upgrade\n",
        "# Do not upgrade until the parser migration lands\nopenai==1.50.0\n",
        "# intentionally old\nopenai>=1.40,<3\n",
    ],
)
def test_intentional_pin_is_held(tmp_path, text):
    (tmp_path / "requirements.txt").write_text(text)
    plan = plan_pins(tmp_path, "openai", "1.92.0")
    assert plan.edits == [] and plan.files == {}
    (hold,) = plan.holds
    assert hold.line == text.count("\n")
    assert hold.comment.startswith("#")
    apply_pins(tmp_path, plan)
    assert (tmp_path / "requirements.txt").read_text() == text


@pytest.mark.parametrize(
    "text",
    [
        "openai==1.50.0  # runtime\n",
        "# runtime deps\nopenai==1.50.0\n",
        "# keep it simple\n\nopenai==1.50.0\n",  # comment not directly above
        "openai==1.50.0  # bookkeeper tooling\n",  # 'keep' only as a whole word
    ],
)
def test_ordinary_comments_still_bump(tmp_path, text):
    (tmp_path / "requirements.txt").write_text(text)
    plan = plan_pins(tmp_path, "openai", "1.92.0")
    assert plan.holds == [] and len(plan.edits) == 1


def test_intent_in_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = [\n    "openai==1.50.0",  # keep: vendored client\n]\n'
        '[tool.poetry.dependencies]\n# pinned on purpose\nopenai = "^1.40"\n'
    )
    plan = plan_pins(tmp_path, "openai", "1.92.0")
    assert [(h.line, h.text) for h in plan.holds] == [(3, "openai==1.50.0"), (7, 'openai = "^1.40"')]
    assert plan.edits == []


def test_satisfied_intentional_pin_is_not_a_hold(tmp_path):
    (tmp_path / "requirements.txt").write_text("openai==2.0.0  # pinned on purpose\n")
    assert plan_pins(tmp_path, "openai", "1.92.0").holds == []


def test_intentional_pin_skips_rule_but_not_others(sample_repo):
    req = sample_repo / "requirements.txt"
    req.write_text("# runtime deps\nopenai==1.50.0  # pinned below 1.92.0 on purpose\npydantic>=2\n")
    before_req = req.read_text()
    before_structured = (sample_repo / "app" / "structured.py").read_text()

    result = run(opts(sample_repo), model=FileModel(GOOD))

    assert req.read_text() == before_req
    assert (sample_repo / "app" / "structured.py").read_text() == before_structured
    (reason,) = result.skipped_rules
    assert "beta-chat-completions-to-ga" in reason
    assert "requirements.txt:2" in reason and "on purpose" in reason
    # rule 2 only needs 1.1.0, which the pin satisfies
    assert {r.rule_id for r in result.rows} == {"functions-to-tools"}
    assert (sample_repo / "app" / "weather.py").read_text() == GOOD.read_text()
    body = result.body
    assert "### Skipped rules" in body
    assert body.index("### Skipped rules") > body.index("| Location")
    assert "`beta-chat-completions-to-ga` (deterministic, not applied)" in body


# --- 2. comment preservation ------------------------------------------------

def test_missing_comments_basic():
    orig = "a = 1  # one\n# two\nb = 2\n"
    assert missing_comments(orig, orig) == []
    assert missing_comments(orig, "a = 1\n# two\nb = 2\n") == [(1, "# one")]
    # moved to another line is fine
    assert missing_comments(orig, "# one\na = 1\n# two\nb = 2\n") == []
    # whole line deleted: its comment may go
    assert missing_comments(orig, "# two\nb = 2\n") == []
    # replaced (not deleted) line must keep its comment
    assert missing_comments(orig, "a = 10\n# two\nb = 2\n") == [(1, "# one")]


def test_missing_comments_counts_duplicates_and_ignores_strings():
    orig = "x = 1  # same\ny = 2  # same\n"
    assert missing_comments(orig, "x = 1  # same\ny = 3\n") == [(2, "# same")]
    assert missing_comments("s = '# not a comment'\n", "s = ''\n") == []


def test_dropped_comment_retried_with_feedback_then_kept(sample_repo):
    model = FileModel(NO_COMMENT, GOOD)
    result = run(opts(sample_repo), model=model)
    assert len(model.requests) == 2
    fb = model.requests[1].feedback
    assert "removed comment" in fb and "line 25: `# legacy function calling`" in fb
    assert (sample_repo / "app" / "weather.py").read_text() == GOOD.read_text()
    assert result.manual == []


def test_dropped_comment_twice_reverted_and_manual(sample_repo):
    original = (sample_repo / "app" / "weather.py").read_text()
    model = FileModel(NO_COMMENT, NO_COMMENT)
    result = run(opts(sample_repo), model=model)
    assert len(model.requests) == 2
    assert (sample_repo / "app" / "weather.py").read_text() == original
    assert [(m.path, m.rule_id) for m in result.manual] == [("app/weather.py", "functions-to-tools")]
    assert "# legacy function calling" in result.manual[0].reason
    assert not [r for r in result.rows if r.rule_id == "functions-to-tools"]


def test_deterministic_rule_keeps_fixture_comments():
    # sanity: the libcst rewrite never trips the same check
    from api_change_agent.codemod import apply_deterministic
    from api_change_agent.rules import load_rules

    src = (SAMPLE_REPO / "app" / "structured.py").read_text()
    new, _ = apply_deterministic(src, load_rules(RULES).rules[0])
    assert missing_comments(src, new) == []
