from api_change_agent.codemod import apply_deterministic, find_sites
from api_change_agent.rules import load_rules
from conftest import FIXTURES, RULES, SAMPLE_REPO

RULESET = load_rules(RULES)
BETA = RULESET.rules[0]
FUNCS = RULESET.rules[1]


def test_golden_rewrite():
    src = (SAMPLE_REPO / "app" / "structured.py").read_text()
    new, edits = apply_deterministic(src, BETA)
    assert new == (FIXTURES / "expected" / "structured.py").read_text()
    assert [(e.line, e.before, e.after) for e in edits] == [
        (16, "client.beta.chat.completions.parse", "client.chat.completions.parse"),
        (25, "client.beta.chat.completions.stream", "client.chat.completions.stream"),
        (36, "client.beta.chat.completions.parse", "client.chat.completions.parse"),
    ]


def test_comments_and_strings_survive():
    src = (SAMPLE_REPO / "app" / "structured.py").read_text()
    new, _ = apply_deterministic(src, BETA)
    assert "# keep this comment" in new
    assert "# inline comment survives" in new
    assert "Docs mention client.beta.chat.completions.parse" in new
    assert "client.chat.completions.create(model=" in new


def test_uncalled_reference_and_receiver_forms():
    src = (
        "import openai\n"
        "fn = self.client.beta.chat.completions.parse  # alias\n"
        "r = make().beta.chat.completions.stream(x=1)\n"
        "q = c.beta.chat.completions.create()\n"
    )
    new, edits = apply_deterministic(src, BETA)
    assert new == (
        "import openai\n"
        "fn = self.client.chat.completions.parse  # alias\n"
        "r = make().chat.completions.stream(x=1)\n"
        "q = c.beta.chat.completions.create()\n"
    )
    assert [e.line for e in edits] == [2, 3]


def test_no_match_is_identity():
    src = "import openai\nx = client.chat.completions.parse()\n"
    assert apply_deterministic(src, BETA) == (src, [])


def test_find_call_sites_with_kwargs():
    src = (SAMPLE_REPO / "app" / "weather.py").read_text()
    sites = find_sites(src, FUNCS)
    assert [s.line for s in sites] == [22]
    assert "functions=" in sites[0].snippet
    other = "import openai\nclient.chat.completions.create(model='m', tools=[])\n"
    assert find_sites(other, FUNCS) == []
    beta_fn = "import openai\nclient.beta.chat.completions.parse(functions=[])\n"
    assert [s.line for s in find_sites(beta_fn, FUNCS)] == [2]


def test_find_attribute_sites():
    src = (SAMPLE_REPO / "app" / "structured.py").read_text()
    assert [s.line for s in find_sites(src, BETA)] == [16, 25, 36]
