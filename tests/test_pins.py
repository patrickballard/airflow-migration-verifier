import pytest

from api_change_agent.pins import apply_pins, bump_spec, plan_pins

MIN = "1.92.0"


@pytest.mark.parametrize(
    "spec, new, status",
    [
        ("", "", "unpinned"),
        ("==1.50.0", "==1.92.0", "bumped"),
        ("==2.1.0", "==2.1.0", "ok"),
        (">=1.40", ">=1.92.0", "bumped"),
        (">=1.40,<3", ">=1.92.0,<3", "bumped"),
        (">= 1.40, < 3", ">= 1.92.0, < 3", "bumped"),
        (">1.0", ">=1.92.0", "bumped"),
        (">=1.95", ">=1.95", "ok"),
        ("~=1.40", "~=1.92", "bumped"),
        ("~=1.40.0", "~=1.92.0", "bumped"),
        ("<3", "<3,>=1.92.0", "bumped"),
        ("!=1.60.0", "!=1.60.0,>=1.92.0", "bumped"),
        ("==1.*", "==1.*,>=1.92.0", "bumped"),
        ("<1.80", "<1.80", "conflict"),
        ("<=1.91.9", "<=1.91.9", "conflict"),
        (">=1.40,<1.90", ">=1.40,<1.90", "conflict"),
        ("===1.50.0", "===1.50.0", "conflict"),
        ("==0.*", "==0.*", "conflict"),
    ],
)
def test_bump_spec(spec, new, status):
    result = bump_spec(spec, MIN)
    assert (result.new, result.status) == (new, status)


@pytest.mark.parametrize(
    "spec, new, status",
    [
        ("^1.40", "^1.92.0", "bumped"),
        ("^2.0", "^2.0", "ok"),
        ("1.50.0", "1.92.0", "bumped"),
        ("*", "*", "unpinned"),
        ("~1.40", "~1.92.0", "bumped"),
        (">=1.40,<2", ">=1.92.0,<2", "bumped"),
    ],
)
def test_bump_poetry_spec(spec, new, status):
    result = bump_spec(spec, MIN, poetry=True)
    assert (result.new, result.status) == (new, status)


def test_requirements_txt_in_place(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "# deps\nopenai==1.50.0  # sdk\nlangchain-openai==0.1.0\nopenai-agents>=0.1\n"
        "-r base.txt\n"
    )
    (tmp_path / "requirements").mkdir()
    (tmp_path / "requirements" / "dev.in").write_text("openai[datalib]>=1.3 ; python_version >= '3.9'\n")
    plan = plan_pins(tmp_path, "openai", MIN)
    assert not plan.conflicts
    assert [(str(e.path), e.line, e.before, e.after) for e in plan.edits] == [
        ("requirements.txt", 2, "openai==1.50.0", "openai==1.92.0"),
        ("requirements/dev.in", 1, "openai[datalib]>=1.3", "openai[datalib]>=1.92.0"),
    ]
    apply_pins(tmp_path, plan)
    assert (tmp_path / "requirements.txt").read_text() == (
        "# deps\nopenai==1.92.0  # sdk\nlangchain-openai==0.1.0\nopenai-agents>=0.1\n-r base.txt\n"
    )
    assert (tmp_path / "requirements" / "dev.in").read_text() == (
        "openai[datalib]>=1.92.0 ; python_version >= '3.9'\n"
    )


def test_pyproject_pep621_and_poetry(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = [\n    "openai>=1.40,<3",\n    "openai-agents==0.1",\n]\n'
        '[project.optional-dependencies]\nai = ["openai ~= 1.40"]\n'
        '[tool.poetry.dependencies]\npython = "^3.11"\nopenai = "^1.40"\n'
        '[tool.poetry.group.dev.dependencies]\nopenai = { version = "1.50.0", extras = ["datalib"] }\n'
    )
    plan = plan_pins(tmp_path, "openai", MIN)
    apply_pins(tmp_path, plan)
    text = (tmp_path / "pyproject.toml").read_text()
    assert '"openai>=1.92.0,<3"' in text
    assert '"openai-agents==0.1"' in text
    assert '"openai ~= 1.92"' in text
    assert 'openai = "^1.92.0"' in text
    assert 'openai = { version = "1.92.0", extras = ["datalib"] }' in text
    assert 'python = "^3.11"' in text
    assert len(plan.edits) == 4


def test_setup_cfg_and_setup_py(tmp_path):
    (tmp_path / "setup.cfg").write_text("[options]\ninstall_requires =\n    openai>=1.0\n    requests\n")
    (tmp_path / "setup.py").write_text(
        "from setuptools import setup\nsetup(install_requires=['openai==1.10.0', 'langchain-openai'])\n"
    )
    plan = plan_pins(tmp_path, "openai", MIN)
    apply_pins(tmp_path, plan)
    assert "    openai>=1.92.0\n" in (tmp_path / "setup.cfg").read_text()
    assert "'openai==1.92.0', 'langchain-openai'" in (tmp_path / "setup.py").read_text()


def test_conflict_reported_not_written(tmp_path):
    (tmp_path / "requirements.txt").write_text("openai<1.80\n")
    plan = plan_pins(tmp_path, "openai", MIN)
    assert plan.edits == []
    assert [(str(c.path), c.line, c.text) for c in plan.conflicts] == [("requirements.txt", 1, "openai<1.80")]


def test_lockfile_notes(tmp_path):
    (tmp_path / "poetry.lock").write_text('[[package]]\nname = "openai"\nversion = "1.50.0"\n')
    (tmp_path / "uv.lock").write_text('[[package]]\nname = "openai"\nversion = "2.0.0"\n')
    plan = plan_pins(tmp_path, "openai", MIN)
    assert [(str(n.path), n.version) for n in plan.lock_notes] == [("poetry.lock", "1.50.0")]


def test_hash_pinned_requirements_are_a_conflict_not_an_edit(tmp_path):
    (tmp_path / "requirements.txt").write_text("openai==1.50.0 \\\n    --hash=sha256:abc\n")
    plan = plan_pins(tmp_path, "openai", MIN)
    assert plan.edits == [] and "hash-pinned" in plan.conflicts[0].reason
