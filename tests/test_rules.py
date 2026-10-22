import pytest
from packaging.version import Version

from api_change_agent.rules import RuleError, load_rules
from conftest import RULES


def test_bundled_rules_load():
    rs = load_rules(RULES)
    assert rs.package == "openai"
    beta, fns, httpx_rule = rs.rules
    assert beta.id == "beta-chat-completions-to-ga"
    assert beta.fix == "deterministic"
    assert beta.min_sdk_version == Version("1.92.0")
    assert beta.transform == {"drop_attribute": "beta"}
    assert beta.changelog.startswith("https://")
    assert fns.fix == "llm"
    assert fns.match.kwargs == ("functions", "function_call")
    assert fns.guidance
    assert httpx_rule.id == "declare-httpx" and httpx_rule.fix == "deterministic"
    assert httpx_rule.match.module == "httpx"
    assert str(httpx_rule.match.sdk_allows) == ">=3.0.0"
    assert httpx_rule.transform == {"add_dependency": "httpx>=0.23.0,<1"}
    assert not httpx_rule.needs_sdk_bump
    assert httpx_rule.note == "openai 3.0 no longer installs httpx; this repo imports it directly"


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
