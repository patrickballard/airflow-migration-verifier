"""action.yml (and the other YAML we ship) must parse, or the Action fails before it starts."""
import re

import pytest
import yaml

from conftest import ROOT

ACTION = ROOT / "action.yml"


def load_action():
    return yaml.safe_load(ACTION.read_text())


def test_action_yml_parses_with_required_keys():
    action = load_action()
    assert isinstance(action, dict)
    for key in ("name", "description", "inputs", "runs"):
        assert action.get(key), f"action.yml is missing {key!r}"
    assert action["runs"]["using"] == "docker"
    assert (ROOT / action["runs"]["image"]).is_file()


def test_every_input_has_a_string_description():
    for name, spec in load_action()["inputs"].items():
        assert isinstance(spec, dict), name
        assert isinstance(spec.get("description"), str) and spec["description"].strip(), name
        if "default" in spec:
            assert isinstance(spec["default"], str), f"{name}: default should be a quoted string"


def test_cli_reads_only_declared_inputs():
    declared = set(load_action()["inputs"])
    cli = (ROOT / "src" / "api_change_agent" / "cli.py").read_text()
    used = set(re.findall(r'_input\("([a-z0-9-]+)"', cli))
    assert used, "no _input() calls found"
    assert used <= declared, f"cli reads undeclared inputs: {sorted(used - declared)}"


@pytest.mark.parametrize(
    "path", sorted(p.relative_to(ROOT).as_posix() for p in ROOT.glob("**/*.y*ml")
                   if ".venv" not in p.parts and "fixtures" not in p.parts)
)
def test_all_shipped_yaml_parses(path):
    assert yaml.safe_load((ROOT / path).read_text()) is not None
