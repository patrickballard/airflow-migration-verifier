# api-change-agent

A Docker GitHub Action that migrates deprecated **openai Python SDK** usage and
opens a PR with a `file:line | before → after | changelog` table. It never
pushes to `main`/`master`, the default branch, or the PR base.

Rules live in [`rules/openai.yaml`](rules/openai.yaml) (default) and [`rules/airflow.yaml`](rules/airflow.yaml). Each rule has a match
pattern, a fix type (`deterministic` or `llm`), a min SDK version and a
changelog URL:

| Rule | Fix | Min openai | What it does |
|---|---|---|---|
| `beta-chat-completions-to-ga` | deterministic (libcst) | 1.92.0 | `client.beta.chat.completions.{parse,stream}` → `client.chat.completions.{parse,stream}` |
| `functions-to-tools` | llm (Claude) | 1.1.0 | `functions=`/`function_call=` → `tools=`/`tool_choice=`, plus the response handling |
| `declare-httpx` | deterministic | (no bump) | openai 3.0 stopped installing `httpx`: if any `.py` file imports `httpx`, the openai pin allows `>=3.0.0` (after this run's bumps), and `httpx` isn't declared, add `httpx>=0.23.0,<1` next to the openai pin, in that file's format |

If the repo pins openai below a rule's minimum, the pin is raised in the same
PR. Supported files: `requirements*.txt`/`.in`, PEP 621 and Poetry
`pyproject.toml`, `setup.cfg`, `setup.py`. A pin whose upper bound excludes
the minimum (`openai<1.80`) skips that rule and the PR says so. Lock files are
flagged, not rewritten.

Only `.py` files that import `openai` are scanned, except by `import:` rules (like `declare-httpx`), which look at every `.py` file. Rewrites go through
**libcst**, so comments and formatting survive.

## Airflow 2 → 3 (`rules: airflow`)

Built from [`research/target-pick`](research/target-pick/REPORT.md). Airflow 2 reached end of life on
2026-04-22. A run:

1. **Raises the pin.** `apache-airflow` goes to 3.3.2 first, so the baseline
   is the old code on Airflow 3.
2. **Builds the test venv** with Airflow's official constraints file.
3. **Runs ruff** `--select AIR3 --preview --unsafe-fixes`. This fixes imports,
   `schedule_interval` and moved operators. Every change ruff makes is
   credited in the PR table ("fixed by ruff AIR301: …"), and whatever it
   can't fix is listed for a human.
4. **Applies our rules** for what ruff leaves: the 14 task-context keys
   Airflow 3 removed, in Python (`context["execution_date"]`,
   `kwargs.get(...)`, `get_current_context()`) and in Jinja (operator
   strings, `.sql`, `.sh`, `.hql`, `.j2` files).
   - **Rewritten:** only keys Airflow's own docs call the *same* value:
     `execution_date` → `logical_date`, `yesterday_ds` / `tomorrow_ds`
     (with `_nodash`) → `macros.ds_add(...)`, `dag_run.execution_date`, and
     `triggering_dataset_events`.
   - **Needs judgment** (Claude, or manual without a key): timetable-dependent
     keys (`next_ds`, `prev_*`, `next_execution_date`, `conf`), a context
     passed around indirectly, or a callable taking `execution_date` as a
     parameter.
5. **Verifies.** For every DAG file it does a `DagBag` import and runs
   `dag.test()`, before and after, on Airflow 3. The PR shows a per-DAG table
   (`❌ not reached: file failed to import` → `✅`). A check that only became
   reachable because an import was fixed doesn't count as a regression.

```yaml
- uses: patrickballard/api-change-agent@main
  with:
    rules: airflow
    anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}   # optional: judgment sites
```

Locally: `python -m api_change_agent --repo . --rules airflow --dry-run --output out --install-command auto --venv /tmp/af3`.

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

## Research

[`research/openai-breaks/`](research/openai-breaks/) has the harness that proved which 1.x → 3.x changes really break: per-version venvs, a mock API server, one snippet per candidate, and the GitHub code-search estimates. See its README.

## Development

```bash
pip install -e '.[test]' && pytest
```

`tests/fixtures/sample_repo` is a small app with its own pytest suite, a fake
client and `openai==1.50.0`. The agent's tests run that suite in a
subprocess, so the test gate is exercised for real. `tests/fixtures/llm`
holds a good and a bad canned Claude patch.
