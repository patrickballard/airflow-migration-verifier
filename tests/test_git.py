import pytest

from api_change_agent.agent import Options, run
from api_change_agent.gitops import UnsafeBranchError, ensure_safe_branch
from conftest import FIXTURE_TEST_COMMAND, FIXTURES, RULES, FakeGitHub, FileModel, git


@pytest.mark.parametrize("branch", ["main", "master", "refs/heads/main", "trunk", "release"])
def test_refuses_protected(branch):
    with pytest.raises(UnsafeBranchError):
        ensure_safe_branch(branch, default_branch="trunk", base_branch="release")


def test_allows_bot_branch():
    ensure_safe_branch("api-change-agent/openai", default_branch="main", base_branch="main")


def options(repo, **kw):
    kw.setdefault("branch", "api-change-agent/openai")
    return Options(
        repo=repo, rules_path=RULES, test_command=FIXTURE_TEST_COMMAND,
        base_branch="main", default_branch="main", repo_slug="acme/app", **kw,
    )


def test_end_to_end_opens_pr_and_never_touches_main(git_repo):
    repo, remote = git_repo
    main_before = git(remote, "rev-parse", "main")
    gh = FakeGitHub()
    logged = []
    result = run(options(repo, log=logged.append), model=FileModel(FIXTURES / "llm" / "weather_good.py"), github=gh)

    assert git(remote, "rev-parse", "main") == main_before
    branch_sha = git(remote, "rev-parse", "api-change-agent/openai")
    assert branch_sha != main_before
    assert git(remote, "rev-parse", f"{branch_sha}^") == main_before
    files = git(remote, "diff", "--name-only", main_before, branch_sha).splitlines()
    assert sorted(files) == ["app/structured.py", "app/weather.py", "requirements.txt"]
    assert sorted(result.changed_files) == sorted(files)
    structured = git(remote, "show", f"{branch_sha}:app/structured.py")
    assert "client.beta.chat.completions.parse(" not in structured
    assert git(remote, "log", "-1", "--format=%an", branch_sha) == "github-actions[bot]"

    assert result.pr_url == "https://github.com/acme/app/pull/7"
    assert any("https://github.com/acme/app/pull/7" in line for line in logged)  # the job log says where the PR is
    (pr,) = gh.created
    assert (pr["head"], pr["base"]) == ("api-change-agent/openai", "main")
    body = pr["body"]
    blob = "https://github.com/acme/app/blob/api-change-agent/openai"
    assert f"[app/structured.py:16]({blob}/app/structured.py#L16)" in body
    assert f"[app/weather.py:25]({blob}/app/weather.py#L25)" in body
    assert f"[requirements.txt:2]({blob}/requirements.txt#L2)" in body
    assert "https://github.com/openai/openai-python/releases/tag/v1.92.0" in body
    # the local checkout is back on main afterwards
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_branch_main_is_refused_before_any_push(git_repo):
    repo, remote = git_repo
    main_before = git(remote, "rev-parse", "main")
    with pytest.raises(UnsafeBranchError):
        run(options(repo, branch="main"), model=FileModel(FIXTURES / "llm" / "weather_good.py"),
            github=FakeGitHub())
    assert git(remote, "rev-parse", "main") == main_before
    assert git(remote, "for-each-ref", "--format=%(refname:short)", "refs/heads").split() == ["main"]


def test_existing_pr_updated_and_noop_rerun(git_repo):
    repo, remote = git_repo
    good = FIXTURES / "llm" / "weather_good.py"
    run(options(repo), model=FileModel(good), github=FakeGitHub())
    first = git(remote, "rev-parse", "api-change-agent/openai")
    # rerun from a clean main: same result, existing PR -> update, no new push
    git(repo, "checkout", "-q", "main")
    git(repo, "reset", "-q", "--hard", "origin/main")
    gh = FakeGitHub(existing={"number": 7, "html_url": "https://github.com/acme/app/pull/7"})
    result = run(options(repo), model=FileModel(good), github=gh)
    assert gh.created == [] and gh.updated[0]["number"] == 7
    assert git(remote, "rev-parse", "api-change-agent/openai") == first
    assert result.pr_url.endswith("/pull/7")


def test_nothing_to_do_opens_nothing(tmp_path):
    (tmp_path / "a.py").write_text("import openai\nclient.chat.completions.parse()\n")
    gh = FakeGitHub()
    result = run(Options(repo=tmp_path, rules_path=RULES, test_command=None, repo_slug="acme/app"), github=gh)
    assert result.rows == [] and gh.created == [] and result.pr_url is None
