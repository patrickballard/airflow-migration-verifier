import libcst as cst
import pytest

from api_change_agent.patterns import ChainPattern, chain_of

BETA = ChainPattern.parse("*.beta.chat.completions.{parse,stream}")


def chain(expr):
    return chain_of(cst.parse_expression(expr))


@pytest.mark.parametrize(
    "expr",
    [
        "client.beta.chat.completions.parse",
        "client.beta.chat.completions.stream",
        "self.client.beta.chat.completions.parse",
        "get_client().beta.chat.completions.parse",
        "openai.beta.chat.completions.parse",
        "clients['a'].beta.chat.completions.stream",
    ],
)
def test_beta_matches(expr):
    assert BETA.matches(chain(expr))


@pytest.mark.parametrize(
    "expr",
    [
        "client.beta.chat.completions.create",
        "client.chat.completions.parse",
        "client.beta.chat.completions",
        "beta.chat.completions.parse",  # '*' needs at least one receiver
        "client.beta.chat.completions.parse.foo",
    ],
)
def test_beta_does_not_match(expr):
    assert not BETA.matches(chain(expr))


def test_middle_wildcard_and_anchored():
    p = ChainPattern.parse("openai.*.create")
    assert p.matches(chain("openai.ChatCompletion.create"))
    assert not p.matches(chain("x.openai.ChatCompletion.create"))
    assert not p.matches(chain("openai.a.b.create"))


def test_segment_index():
    assert BETA.index_from_end("beta") == 4
    assert BETA.index_from_end("chat") == 3
    with pytest.raises(ValueError):
        BETA.index_from_end("nope")


def test_bad_pattern():
    with pytest.raises(ValueError):
        ChainPattern.parse("client..parse")
