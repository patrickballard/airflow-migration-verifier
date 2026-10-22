"""Rule `declare-httpx`: openai 3.0 stopped installing httpx."""
import shlex
import sys
import textwrap

import pytest
import yaml
from packaging.specifiers import SpecifierSet

from api_change_agent.agent import Options, run
from api_change_agent.codemod import find_sites
from api_change_agent.deps import declarations, plan_add_dependency, spec_allows
from api_change_agent.rules import load_rules
from conftest import FIXTURE_TEST_COMMAND, RULES

REQ = "httpx>=0.23.0,<1"
V3 = SpecifierSet(">=3.0.0")
RULE = load_rules(RULES).rules[2]
NOTE = "openai 3.0 no longer installs httpx; this repo imports it directly"


def write(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"))


def plan(root):
    return plan_add_dependency(root, "openai", REQ, V3)


# --- matching ---------------------------------------------------------------

@pytest.mark.parametrize("spec, poetry, allows", [
    ("", False, True), ("==1.50.0", False, False), (">=1.40", False, True), ("<2", False, False),
    ("==3.36.1", False, True), ("~=3.1", False, True), ("==3.*", False, True), ("~=2.5", False, False),
    (">=2.9,<3.1,!=3.0.0", False, True), ("^1.40", True, False), ("^3.0", True, True), ("~2.9", True, False),
    ("3.36.1", True, True), ("", True, True), ("==2.*", False, False),
])
def test_spec_allows_v3(spec, poetry, allows):
    assert spec_allows(spec, V3, poetry) is allows


@pytest.mark.parametrize("src, lines", [
    ("import httpx\n", [1]),
    ("from httpx import Client\n", [1]),
    ("import os, httpx as h\n", [1]),
    ("import httpx._transports.default\n", [1]),
    ("def f():\n    import httpx\n", [2]),
    ("import httpx_sse\nimport httpx2\nfrom . import httpx\nx = 'import httpx'\n", []),
])
def test_import_sites(src, lines):
    assert [s.line for s in find_sites(src, RULE)] == lines


def test_declarations_ignore_lookalikes_and_non_dependency_keys(tmp_path):
    write(tmp_path, {
        "requirements.txt": """
            # httpx is great
            -r base.txt
            httpx-sse==0.4
            httpx2>=2
            httpx_ws
            """,
        "pyproject.toml": """
            [build-system]
            requires = ["httpx"]
            [project]
            name = "httpx"
            dependencies = ["openai"]
            """,
    })
    assert declarations(tmp_path, "httpx") == []
    assert [(str(d.path), d.line) for d in declarations(tmp_path, "openai")] == [("pyproject.toml", 5)]


# --- insertion, per file format -----------------------------------------------

@pytest.mark.parametrize("name, before, after, line, new_line", [
    ("requirements.txt",
     "# runtime\nopenai==3.36.1  # sdk\npydantic>=2\n",
     "# runtime\nopenai==3.36.1  # sdk\nhttpx>=0.23.0,<1\npydantic>=2\n", 3, True),
    ("requirements/base.in",
     "openai>=1.40 ; python_version >= '3.9'\n",
     "openai>=1.40 ; python_version >= '3.9'\nhttpx>=0.23.0,<1\n", 2, True),
    ("pyproject.toml",
     '[project]\ndependencies = [\n    "openai>=1.40",\n    "pydantic",\n]\n',
     '[project]\ndependencies = [\n    "openai>=1.40",\n    "httpx>=0.23.0,<1",\n    "pydantic",\n]\n', 4, True),
    ("pyproject.toml",
     '[project]\ndependencies = [\n    "pydantic",\n    "openai>=1.40"  # sdk\n]\n',
     '[project]\ndependencies = [\n    "pydantic",\n    "openai>=1.40",  # sdk\n    "httpx>=0.23.0,<1"\n]\n', 5, True),
    ("pyproject.toml",
     '[project]\ndependencies = ["openai>=1.40", "pydantic"]\n',
     '[project]\ndependencies = ["openai>=1.40", "httpx>=0.23.0,<1", "pydantic"]\n', 2, False),
    ("pyproject.toml",
     '[project.optional-dependencies]\nai = ["openai"]\n',
     '[project.optional-dependencies]\nai = ["openai", "httpx>=0.23.0,<1"]\n', 2, False),
    ("pyproject.toml",
     '[tool.poetry.dependencies]\npython = "^3.11"\nopenai = "^3.0"\n',
     '[tool.poetry.dependencies]\npython = "^3.11"\nopenai = "^3.0"\nhttpx = ">=0.23.0,<1"\n', 4, True),
    ("setup.cfg",
     "[options]\ninstall_requires =\n    openai>=3\n    requests\n",
     "[options]\ninstall_requires =\n    openai>=3\n    httpx>=0.23.0,<1\n    requests\n", 4, True),
    ("setup.py",
     "setup(install_requires=['openai', 'requests'])\n",
     "setup(install_requires=['openai', 'httpx>=0.23.0,<1', 'requests'])\n", 1, False),
])
def test_insertion_matches_file_format(tmp_path, name, before, after, line, new_line):
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text(before)
    p = plan(tmp_path)
    (edit,) = p.edits
    assert (str(edit.path), edit.line, edit.new_line) == (name, line, new_line)
    assert p.files[edit.path] == after


def test_backslash_continuation_kept_intact(tmp_path):
    write(tmp_path, {"requirements.txt": "openai==3.36.1 \\\n    ; python_version >= '3.9'\nrich\n"})
    assert plan(tmp_path).files[next(iter(plan(tmp_path).files))] == (
        "openai==3.36.1 \\\n    ; python_version >= '3.9'\nhttpx>=0.23.0,<1\nrich\n")


def test_hash_pinned_file_is_not_edited(tmp_path):
    write(tmp_path, {"requirements.txt": "openai==3.36.1 --hash=sha256:abc\n"})
    p = plan(tmp_path)
    assert p.files == {} and "--hash" in p.problems[0].reason


def test_every_file_that_declares_openai_gets_it(tmp_path):
    write(tmp_path, {"requirements.txt": "openai>=1.40\n", "pyproject.toml": '[project]\ndependencies = ["openai"]\n'})
    assert sorted(str(e.path) for e in plan(tmp_path).edits) == ["pyproject.toml", "requirements.txt"]


@pytest.mark.parametrize("files", [
    {"requirements.txt": "openai>=1.40\n", "requirements-dev.txt": "httpx==0.27.0\n"},
    {"requirements.txt": "openai>=1.40\nhttpx[http2]\n"},
    {"pyproject.toml": '[tool.poetry.dependencies]\nopenai = "^3.0"\nhttpx = {version = "^0.27", optional = true}\n'},
    {"setup.cfg": "[options]\ninstall_requires = openai\n[options.extras_require]\ntest =\n    httpx\n"},
])
def test_already_declared(tmp_path, files):
    write(tmp_path, files)
    p = plan(tmp_path)
    assert p.declared and p.edits == []


def test_lookalike_declared_still_adds(tmp_path):
    write(tmp_path, {"requirements.txt": "openai>=1.40\nhttpx-sse\n"})
    assert len(plan(tmp_path).edits) == 1


@pytest.mark.parametrize("pin", ["openai==1.50.0", "openai<3", "openai~=2.5"])
def test_not_triggered_when_pin_excludes_v3(tmp_path, pin):
    write(tmp_path, {"requirements.txt": pin + "\n"})
    p = plan(tmp_path)
    assert p.sdk and not p.triggering and p.edits == []


def test_intentional_absence_is_respected(tmp_path):
    write(tmp_path, {"requirements.txt": "openai>=1.40\n# httpx intentionally not declared: vendored copy\n"})
    p = plan(tmp_path)
    assert p.holds and p.edits == []


def test_lock_and_compiled_notes(tmp_path):
    write(tmp_path, {"pyproject.toml": '[project]\ndependencies = ["openai"]\n', "uv.lock": "",
                     "requirements.in": "openai\n", "requirements.txt": "openai==3.36.1\n"})
    notes = " ".join(plan(tmp_path).notes)
    assert "uv.lock" in notes and "pip-compile" in notes


# --- end to end through the agent ------------------------------------------------

def repo_with(sample_repo, requirements, *, net="import httpx\n\n\ndef ping(url):\n    return httpx.get(url)\n"):
    (sample_repo / "requirements.txt").write_text(requirements)
    if net is not None:
        (sample_repo / "app" / "net.py").write_text(net)  # imports httpx, not openai
    return sample_repo


def opts(repo, **kw):
    kw.setdefault("test_command", FIXTURE_TEST_COMMAND)
    return Options(repo=repo, rules_path=kw.pop("rules_path", RULES), dry_run=True, **kw)


def httpx_rows(result):
    return [r for r in result.rows if r.rule_id == "declare-httpx"]


def test_adds_httpx_when_pin_allows_v3(sample_repo):
    repo = repo_with(sample_repo, "# runtime deps\nopenai>=1.40\npydantic>=2\n")
    result = run(opts(repo))
    assert (repo / "requirements.txt").read_text() == (
        "# runtime deps\nopenai>=1.92.0\nhttpx>=0.23.0,<1\npydantic>=2\n")
    (row,) = httpx_rows(result)
    assert (row.path, row.line, row.before, row.after) == ("requirements.txt", 3, "", REQ)
    assert row.note == f"{NOTE} (`app/net.py:1`)"
    pin = [r for r in result.rows if r.rule_id == "beta-chat-completions-to-ga" and r.path == "requirements.txt"]
    assert [(r.line, r.after) for r in pin] == [(2, "openai>=1.92.0")]
    assert "<i>(not declared)</i> → <code>httpx&gt;=0.23.0,&lt;1</code><br><sub>" + NOTE in result.body
    assert "`declare-httpx` (deterministic, applied)" in result.body and "No `openai` bump needed." in result.body
    assert result.tests_after.passed and result.new_failures == set()


def test_not_triggered_below_v3_and_explained(sample_repo):
    repo = repo_with(sample_repo, "openai==1.50.0\npydantic>=2\n")
    result = run(opts(repo))
    assert "httpx" not in (repo / "requirements.txt").read_text()
    assert httpx_rows(result) == []
    assert "not needed yet" in result.rule_notes["declare-httpx"]
    assert "`app/net.py:1`" in result.rule_notes["declare-httpx"]


def test_intentional_v3_pin_is_left_alone_but_httpx_added(sample_repo):
    repo = repo_with(sample_repo, "openai==3.36.1  # pinned on purpose\npydantic>=2\n")
    result = run(opts(repo, test_command=None))
    assert (repo / "requirements.txt").read_text() == (
        "openai==3.36.1  # pinned on purpose\nhttpx>=0.23.0,<1\npydantic>=2\n")
    assert result.skipped_rules == []  # 3.36.1 already satisfies the beta rule; nothing held
    assert {r.rule_id for r in result.rows} >= {"declare-httpx", "beta-chat-completions-to-ga"}


def test_held_pin_below_v3_does_not_trigger(sample_repo):
    repo = repo_with(sample_repo, "openai==1.50.0  # keep\n")
    result = run(opts(repo, test_command=None))
    assert (repo / "requirements.txt").read_text() == "openai==1.50.0  # keep\n"
    assert any("beta-chat-completions-to-ga" in s for s in result.skipped_rules)
    assert httpx_rows(result) == [] and "not needed yet" in result.rule_notes["declare-httpx"]


def test_triggered_by_this_runs_bump_to_v3(sample_repo, tmp_path):
    data = yaml.safe_load(RULES.read_text())
    data["rules"][0]["min_sdk_version"] = "3.0.0"  # pretend the beta rule needs 3.x
    rules = tmp_path / "rules.yaml"
    rules.write_text(yaml.safe_dump(data))
    repo = repo_with(sample_repo, "openai==1.50.0\npydantic>=2\n")
    result = run(opts(repo, rules_path=rules, test_command=None))
    assert (repo / "requirements.txt").read_text() == "openai==3.0.0\nhttpx>=0.23.0,<1\npydantic>=2\n"
    assert [r.line for r in httpx_rows(result)] == [2]


def test_import_anywhere_counts_and_none_means_no_rule(sample_repo):
    repo = repo_with(sample_repo, "openai>=1.40\n", net=None)
    result = run(opts(repo, test_command=None))
    assert "declare-httpx" not in {r.rule_id for r in result.rows}
    assert "httpx" not in (repo / "requirements.txt").read_text()


def test_already_declared_noted(sample_repo):
    repo = repo_with(sample_repo, "openai>=1.40\nhttpx\n")
    result = run(opts(repo, test_command=None))
    assert httpx_rows(result) == []
    assert result.rule_notes["declare-httpx"] == "`httpx` is already declared at `requirements.txt:2`."


def test_reverted_when_tests_regress(sample_repo):
    repo = repo_with(sample_repo, "openai>=1.40\n")
    # a "test suite" that fails exactly when httpx gets declared
    check = f"{shlex.quote(sys.executable)} -c \"import sys; sys.exit('httpx' in open('requirements.txt').read())\""
    result = run(opts(repo, test_command=check))
    assert (repo / "requirements.txt").read_text() == "openai>=1.92.0\n"  # pin bump kept, httpx line reverted
    assert httpx_rows(result) == []
    (item,) = [m for m in result.manual if m.rule_id == "declare-httpx"]
    assert item.path == "requirements.txt" and "broke tests" in item.reason
