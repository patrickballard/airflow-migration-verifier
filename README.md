# api-change-agent

A Docker GitHub Action that migrates deprecated **openai Python SDK** usage and
opens a PR with a `file:line | before → after | changelog` table. It never
pushes to `main`/`master`, the default branch, or the PR base.

Rules live in [`rules/openai.yaml`](rules/openai.yaml). Each rule has a match
pattern, a fix type (`deterministic` or `llm`), a min SDK version and a
changelog URL:

| Rule | Fix | Min openai | What it does |
|---|---|---|---|
| `beta-chat-completions-to-ga` | deterministic (libcst) | 1.92.0 | `client.beta.chat.completions.{parse,stream}` → `client.chat.completions.{parse,stream}` |
| `functions-to-tools` | llm (Claude) | 1.1.0 | `functions=`/`function_call=` → `tools=`/`tool_choice=`, plus the response handling |

If the repo pins openai below a rule's minimum, the pin is raised in the same
PR. Supported files: `requirements*.txt`/`.in`, PEP 621 and Poetry
`pyproject.toml`, `setup.cfg`, `setup.py`. A pin whose upper bound excludes
the minimum (`openai<1.80`) skips that rule and the PR says so. Lock files are
flagged, not rewritten.

Only `.py` files that import `openai` are scanned. Rewrites go through
**libcst**, so comments and formatting survive.

## Probe result: is the beta path broken?

No. On openai **3.36.1** (latest, 2026-10-22),
`client.beta.chat.completions.parse` / `.stream` work with **no warning**
(sync and async). `Beta.chat` returns the same `Chat` resource as
`client.chat`. The GA methods first appear in **1.92.0**: 1.91.0 has only the
beta path. So Rule 1 is forward-compat cleanup, and the PR text says so.
`functions=` / `function_call=` are also still accepted; they're deprecated
in docstrings only. Reproduce with `python scripts/probe_beta_path.py`.

## How a run works

1. Run the repo's tests once as a baseline. They run in their own venv,
   which `install-command` sets up; secrets are stripped from the test
   environment.
2. Apply deterministic rules, bump pins, reinstall, and re-test. A
   regression reverts the stage.
3. For each LLM file, Claude returns the full file through a forced tool
   call. The patch must parse, must leave no flagged site, and must not add
   **new** test failures compared with the baseline (pytest junit, per test
   ID). Failures are fed back for another attempt; after
   `max-llm-attempts` the file is reverted and listed under "Needs manual
   migration".
4. Commit as `github-actions[bot]` onto `api-change-agent/openai` and push
   with an explicit refspec and `--force-with-lease`. Then open the PR, or
   update the existing one. If the tree is unchanged, nothing is pushed.

Usage: [`examples/workflow.yml`](examples/workflow.yml). `dry-run: true`
prints the report to the step summary without touching git.

## Limits

- Aliased chains aren't followed (`c = client.beta.chat.completions; c.parse()`).
- Files that use a client without importing `openai` are skipped, per the
  scan rule.
- LLM patches are only as safe as the repo's tests. If the baseline test run
  fails without per-test results, the LLM rule is skipped and the PR is
  marked untested.
- The branch is cut from the checked-out ref. Run the action on the default
  branch (`schedule` / `workflow_dispatch`).

## Development

```bash
pip install -e '.[test]' && pytest
```

`tests/fixtures/sample_repo` is a small app with its own pytest suite, a fake
client and `openai==1.50.0`. The agent's tests run that suite in a
subprocess, so the test gate is exercised for real. `tests/fixtures/llm`
holds a good and a bad canned Claude patch.
