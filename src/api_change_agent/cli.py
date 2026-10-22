"""Entry point for the Docker action and local runs.

Action inputs arrive as ``INPUT_<NAME>`` env vars; flags override them.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .agent import DEFAULT_BRANCH, Options, run
from .github_api import GitHubClient
from .gitops import UnsafeBranchError
from .llm import DEFAULT_MODEL, ClaudePatchModel

# src layout during development; /app/rules inside the action image
_RULE_DIRS = (Path(__file__).resolve().parents[2] / "rules", Path("/app/rules"))
BUNDLED_RULES = next((d / "openai.yaml" for d in _RULE_DIRS if (d / "openai.yaml").exists()),
                     _RULE_DIRS[-1] / "openai.yaml")


def _input(name: str, default: str | None = None) -> str | None:
    for key in (f"INPUT_{name.upper()}", f"INPUT_{name.upper().replace('-', '_')}"):
        value = os.environ.get(key)
        if value not in (None, ""):
            return value
    return default


def _bool(value: str | None) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _event_default_branch() -> str | None:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not os.path.exists(path):
        return None
    try:
        return json.loads(Path(path).read_text()).get("repository", {}).get("default_branch")
    except ValueError:
        return None


def parse_args(argv=None) -> argparse.Namespace:
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    p = argparse.ArgumentParser(prog="api-change-agent", description=__doc__)
    p.add_argument("--repo", default=os.environ.get("GITHUB_WORKSPACE", "."))
    p.add_argument("--rules", default=_input("rules"), help="rules YAML (default: bundled openai rules)")
    p.add_argument("--dry-run", action="store_true", default=_bool(_input("dry-run", "false")))
    p.add_argument("--test-command", default=_input("test-command", "auto"),
                   help="'auto' to detect pytest, 'none' to skip tests")
    p.add_argument("--install-command", default=_input("install-command", "auto" if in_actions else None))
    p.add_argument("--venv", default=_input("venv-dir", "/tmp/aca-venv" if in_actions else None))
    p.add_argument("--branch", default=_input("branch", DEFAULT_BRANCH))
    p.add_argument("--base-branch", default=_input("base-branch"))
    p.add_argument("--model", default=_input("model", DEFAULT_MODEL))
    p.add_argument("--max-llm-attempts", type=int, default=int(_input("max-llm-attempts", "2")))
    p.add_argument("--exclude", action="append", default=[
        x.strip() for x in (_input("exclude") or "").replace("\n", ",").split(",") if x.strip()
    ])
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    repo = Path(args.repo).resolve()
    rules = Path(args.rules) if args.rules else BUNDLED_RULES
    if not rules.is_absolute() and not rules.exists():
        rules = repo / rules

    if os.environ.get("GITHUB_ACTIONS") == "true":
        subprocess.run(["git", "config", "--global", "--add", "safe.directory", str(repo)], check=False)

    api_key = _input("anthropic-api-key") or os.environ.get("ANTHROPIC_API_KEY")
    model = None
    if api_key:
        import anthropic

        model = ClaudePatchModel(client=anthropic.Anthropic(api_key=api_key), model=args.model)

    slug = os.environ.get("GITHUB_REPOSITORY")
    token = _input("github-token") or os.environ.get("GITHUB_TOKEN")
    github = None
    if slug and token and not args.dry_run:
        github = GitHubClient(slug, token, os.environ.get("GITHUB_API_URL", "https://api.github.com"))

    test_command = None if str(args.test_command).lower() in ("", "none", "false", "off") else args.test_command
    options = Options(
        repo=repo, rules_path=rules, dry_run=args.dry_run, test_command=test_command,
        install_command=args.install_command, venv_dir=Path(args.venv) if args.venv else None,
        max_llm_attempts=args.max_llm_attempts, branch=args.branch, base_branch=args.base_branch,
        default_branch=_event_default_branch(), repo_slug=slug,
        server_url=os.environ.get("GITHUB_SERVER_URL", "https://github.com"), exclude=tuple(args.exclude),
    )
    try:
        result = run(options, model=model, github=github)
    except UnsafeBranchError as e:
        print(f"::error::{e}")
        return 1

    print(result.body)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as f:
            f.write(result.body + "\n")
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as f:
            f.write(f"pr-url={result.pr_url or ''}\n")
            f.write(f"changes={len(result.rows)}\n")
            f.write(f"manual={len(result.manual)}\n")
    for m in result.manual:
        print(f"::warning file={m.path},line={m.line}::{m.rule_id}: {m.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
