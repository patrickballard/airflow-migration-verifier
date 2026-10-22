import pytest

from api_change_agent.scanner import imports_package, scan_repo


@pytest.mark.parametrize(
    "src",
    [
        "import openai\n",
        "import openai.types as t\n",
        "from openai import OpenAI\n",
        "from openai.types.chat import ChatCompletion\n",
        "import os, openai\n",
        "def f():\n    import openai\n",
        "try:\n    from openai import OpenAI as O\nexcept ImportError:\n    O = None\n",
    ],
)
def test_detects_import(src):
    assert imports_package(src, "openai")


@pytest.mark.parametrize(
    "src",
    [
        "import openai_agents\n",
        "from langchain_openai import ChatOpenAI\n",
        "from . import openai\n",
        "x = 'import openai'\n",
        "# import openai\n",
        "client.beta.chat.completions.parse()\n",
    ],
)
def test_ignores_non_imports(src):
    assert not imports_package(src, "openai")


def test_scan_fixture_only_openai_files(sample_repo):
    (sample_repo / ".venv" / "lib").mkdir(parents=True)
    (sample_repo / ".venv" / "lib" / "x.py").write_text("import openai\n")
    (sample_repo / "broken.py").write_text("import openai\ndef (:\n")
    (sample_repo / "skipme").mkdir()
    (sample_repo / "skipme" / "y.py").write_text("import openai\n")
    result = scan_repo(sample_repo, "openai", exclude=["skipme/*"])
    assert sorted(str(p) for p in result.files) == ["app/structured.py", "app/weather.py"]
    assert [str(p) for p in result.unparsable] == ["broken.py"]
