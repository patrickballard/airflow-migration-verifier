import pytest
from packaging.version import Version

from api_change_agent.rules import RuleError, load_rules
from conftest import RULES


def test_bundled_rules_load():
    rs = load_rules(RULES)
    assert rs.package == "openai"
    beta, fns = rs.rules
    assert beta.id == "beta-chat-completions-to-ga"
    assert beta.fix == "deterministic"
    assert beta.min_sdk_version == Version("1.92.0")
    assert beta.transform == {"drop_attribute": "beta"}
    assert beta.changelog.startswith("https://")
    assert fns.fix == "llm"
    assert fns.match.kwargs == ("functions", "function_call")
    assert fns.guidance


@pytest.mark.parametrize(
    "body, message",
    [
        ("fix: magic", "fix"),
        ("fix: deterministic", "transform"),
        ("fix: llm\n    min_sdk_version: not-a-version", "min_sdk_version"),
        ("fix: llm\n    changelog: ftp://x", "changelog"),
    ],
)
def test_invalid_rules_rejected(tmp_path, body, message):
    p = tmp_path / "r.yaml"
    p.write_text(
        "package: openai\nrules:\n  - id: r\n    match: {call: '*.x'}\n"
        "    min_sdk_version: '1.0'\n    changelog: https://example.com\n    " + body + "\n"
    )
    # later keys win in YAML, so the overrides above take effect
    with pytest.raises(RuleError, match=message):
        load_rules(p)


def test_match_needs_attribute_or_call(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text(
        "package: openai\nrules:\n  - id: r\n    match: {}\n    fix: llm\n"
        "    min_sdk_version: '1.0'\n    changelog: https://example.com\n"
    )
    with pytest.raises(RuleError, match="match"):
        load_rules(p)
