import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
SAMPLE_REPO = FIXTURES / "sample_repo"
# A test-only ruleset for the generic rule engine (patterns, pins, add_dependency, LLM flow); not shipped.
RULES = FIXTURES / "rules" / "openai.yaml"
# Run the fixture repo's own suite with this interpreter (it has openai + pydantic).
FIXTURE_TEST_COMMAND = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider"


@pytest.fixture(autouse=True)
def no_real_credentials(tmp_path_factory, monkeypatch):
    """No test may see the developer's real Anthropic key (env var or key file) or spend money."""
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("INPUT_ANTHROPIC-API-KEY", raising=False)
    monkeypatch.delenv("INPUT_ANTHROPIC_API_KEY", raising=False)
    # in CI these point at the real job's summary and outputs: a test must not write into them
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)


@pytest.fixture
def sample_repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(SAMPLE_REPO, dest)
    return dest


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def git_repo(sample_repo, tmp_path):
    """The fixture repo committed on main and pushed to a local bare remote."""
    remote = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(sample_repo, "init", "-q", "-b", "main")
    git(sample_repo, "config", "user.email", "dev@example.com")
    git(sample_repo, "config", "user.name", "Dev")
    git(sample_repo, "add", "-A")
    git(sample_repo, "commit", "-q", "-m", "initial")
    git(sample_repo, "remote", "add", "origin", str(remote))
    git(sample_repo, "push", "-q", "origin", "main")
    return sample_repo, remote


class FileModel:
    """PatchModel fake that answers with canned files, one per call."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def propose(self, request):
        self.requests.append(request)
        answer = self.answers[min(len(self.requests), len(self.answers)) - 1]
        return answer.read_text() if isinstance(answer, Path) else answer


class FakeGitHub:
    def __init__(self, existing=None):
        self.existing = existing
        self.created = []
        self.updated = []

    def find_open_pr(self, head):
        return self.existing

    def create_pr(self, *, head, base, title, body):
        self.created.append(dict(head=head, base=base, title=title, body=body))
        return {"number": 7, "html_url": "https://github.com/acme/app/pull/7"}

    def update_pr(self, number, *, title, body):
        self.updated.append(dict(number=number, title=title, body=body))
        return {"number": number, "html_url": f"https://github.com/acme/app/pull/{number}"}
