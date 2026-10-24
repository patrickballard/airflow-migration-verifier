**Airflow Migration Verifier** verifies any Airflow 2→3 migration, including ones done by other tools, by rendering your templates on both versions — no tests needed.

[![CI](https://github.com/patrickballard/api-change-agent-test/actions/workflows/ci.yml/badge.svg)](https://github.com/patrickballard/api-change-agent-test/actions/workflows/ci.yml)

## Airflow 3 silently changes the date your cron DAGs process

Take an `@daily` DAG whose task runs `process {{ ds }}`, and the run that fires at 2024-05-02 00:00 UTC:

| | Default timetable | The task runs |
|---|---|---|
| Airflow 2.11.2 | `CronDataIntervalTimetable` | `process 2024-05-01` |
| Airflow 3.3.2 | `CronTriggerTimetable` | `process 2024-05-02` |

There is no error and no warning. In Airflow 3 a cron run's logical date is the moment it fires, not the start of the interval it covers. So `{{ ds }}`, `logical_date` and every partition path built from them move by one interval. Both rows were rendered on the real versions.

This GitHub Action checks a migration the same way, whoever made it: a person, ruff, another tool or an AI agent. It renders every template field with the old code on Airflow 2 and the new code on Airflow 3, run by run, and checks that each DAG imports and uses nothing Airflow 3 removed. It can also do the migration itself, in a pull request, proving each change the same way. Whatever it can't prove, it labels as unverified. [See a real PR it opened.](https://github.com/patrickballard/api-change-agent-test/pull/2)

## What it does

1. **ruff's AIR3 fixes** (`--select AIR3 --preview`): imports, `schedule_interval`, moved operators. Every change ruff makes is credited in the PR table.
   - When ruff swaps in a different operator class, every call's keyword arguments are checked against that class's signature on Airflow 3. One it no longer accepts (`MySqlOperator(mysql_conn_id=...)` → `SQLExecuteQueryOperator`, which takes `conn_id=`) is listed for a human, because Airflow 3 rejects unknown arguments when it parses the DAG file.
2. **What ruff misses:**
   - **Timetable pin.** `schedule="@daily"` becomes `CronDataIntervalTimetable("@daily", timezone=...)`, so runs keep their Airflow 2 dates.
     - The zone comes from the DAG's `start_date`, then `default_args`, then the repo's `default_timezone` (`airflow.cfg`, `.env`, docker-compose, Helm, Dockerfile).
     - The PR also names the global alternative, `[scheduler] create_cron_data_intervals = True`.
   - **`days_ago(N)`** becomes `pendulum.today("UTC").add(days=-N)`. That's the same moment, still computed at parse time. A fixed date would change scheduling and catchup.
   - **Removed context keys,** in Python (`context["execution_date"]`) and in Jinja (operator strings, `.sql` / `.sh` / `.j2` files). They're rewritten only where Airflow's docs say the replacement is the same value: `execution_date` → `logical_date`, `yesterday_ds` → `macros.ds_add(ds, -1)`, …
   - **Removed DAG methods** (`following_schedule`, `previous_schedule` and 50 others, checked against both versions) are flagged. A DAG that calls one still imports, then fails when the call runs.
   - **Judgment sites** (`next_ds`, `prev_ds`, computed schedules) go to Claude when you give it a key, with a hard spend cap. Otherwise they're listed for a human.
3. **A render check that proves each change.**
   - Every template field of every affected DAG is rendered with the old code on Airflow 2.11.2 and the new code on 3.3.2.
   - The two sides are compared for runs paired by the moment they fire: 23:00, 00:00, month and year end, leap day, both DST switches and about 20 ordinary runs.
   - A rule-based change that renders differently is reverted and listed with the mismatching runs. A Claude patch gets the mismatch as feedback and another try.
4. **DAG checks.** Each DAG file gets a `DagBag` import and each DAG a `dag.test()`, before and after, on Airflow 3.
5. **Nothing unproven is presented as done.**
   - A file that fails on Airflow 3 both before and after gets no unsafe fixes and no Claude patches, and is listed under *Unverified files* with its error.
   - A cron DAG left unpinned is listed under *Run dates will shift*, with the render mismatch as evidence.

## Proof: 10 public repos

These were dry runs on cloned repos. Nothing was pushed to them.
- [mozilla/telemetry-airflow](https://github.com/mozilla/telemetry-airflow)
- the *Data Pipelines with Apache Airflow* book code
- 8 others

[research/airflow-real-world/SCORECARD.md](https://github.com/patrickballard/airflow-migration-verifier/blob/main/research/airflow-real-world/SCORECARD.md) has the per-repo table, the diffs' verdicts and the scripts.

- **161 DAGs** load on Airflow 2.11.2 and fail to import on Airflow 3.3.2 as written.
- After the agent, **155 of them import on Airflow 3**. Two of those call `DAG.following_schedule`, which Airflow 3 removed; the agent flags them as manual.
- **105** of the other 153 have template fields. **75** of those run on a schedule and were render-checked. **73 render identically** to Airflow 2 on every sampled run. The other 2 are real breaks the agent reports instead of guessing: one uses `prev_ds`, which has no exact equivalent, and one uses `dag_run.external_trigger`, which Airflow 3 removed.
- **30** have no schedule (`schedule=None`), so there are no runs to compare. They're reported as not render-checked and aren't counted as proven.
- **Headline: 121 of 161 (75%)** import, render identically and call no removed method. Claude's patches in it cost $0.60; the rest are rule-based.
- An earlier version of this page said 144. That was measured with a render check that counted unscheduled DAGs, and DAGs whose sampled runs never lined up, as identical. Both are fixed; see [findings](https://github.com/patrickballard/airflow-migration-verifier/blob/main/research/airflow-real-world/findings.md).
- **Other tools' migrations, verified the same way:** on the same 161 DAGs, ruff alone, airflow-v2-to-v3 and airflow-migration-helper each pass all checks for 24–25. See [the competitor check](https://github.com/patrickballard/airflow-migration-verifier/blob/main/research/competitor-check/REPORT.md), which also says what they do better.
- **Diffs reviewed by hand:** no wrong change in any verified file. One wrong ruff fix keeps `mysql_conn_id=` when it swaps `MySqlOperator` for `SQLExecuteQueryOperator`. It sits in a file that fails on Airflow 3 before and after, and the PR labels it unverified. Since v1.1.0 the kwarg check also flags it directly: 37 rejected `mysql_conn_id=` arguments in 9 files, re-checked on that repo's saved tree.

## Quickstart

Both workflows below go on your **default branch**. The action needs nothing installed and no secrets.

### Verify a migration pull request (made by anything)

Add `.github/workflows/verify-airflow-3.yml`. It compares the PR's base commit (Airflow 2) with its head commit (Airflow 3) and changes nothing:

```yaml
name: verify-airflow-3-migration
on: pull_request

permissions:
  contents: read

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with: {ref: "${{ github.event.pull_request.base.sha }}", path: before}
      - uses: actions/checkout@v7
        with: {ref: "${{ github.event.pull_request.head.sha }}", path: after}
      - uses: actions/cache/restore@v6   # reuse the Airflow 2 and 3 venvs from earlier runs
        id: venvs
        with:
          path: ${{ runner.temp }}/_github_home/aca-venv*
          key: aca-venvs-v1-${{ hashFiles('before/**/requirements*.txt', 'after/**/requirements*.txt') }}
      - uses: patrickballard/airflow-migration-verifier@v1
        with:
          mode: verify
          before: before
          after: after
          strict: "true"           # fail the check when the migration breaks something (list below)
      - uses: actions/cache/save@v6      # saved even when the check fails, so a re-run starts warm
        if: always() && steps.venvs.outputs.cache-hit != 'true'
        with:
          path: ${{ runner.temp }}/_github_home/aca-venv*
          key: ${{ steps.venvs.outputs.cache-primary-key }}
```

It runs on every PR opened or updated after you commit it. For a PR that's already open, push a commit to it, or close and reopen it.

**The report** is on the run's *Summary* page (Actions tab → the run), and in the action step's log. It lists:
- each DAG file's import on Airflow 3, before and after;
- every template field rendered on Airflow 2 (base) and Airflow 3 (head), run by run;
- removed context keys and template variables still in use, removed DAG methods, and arguments Airflow 3 rejects;
- cron and timedelta schedules whose run dates will shift.

With `strict: "true"` the check fails on any of these except the schedules, which are reported as warnings. Verify mode imports each DAG file but doesn't run the DAGs: running someone's tasks in a PR check would run their side effects.

**What it builds:** an Airflow 3 venv from the head's requirements, and an Airflow 2 venv at the base's `apache-airflow` pin (2.11.2 if there's none).

**Scheduler setting:** if the head sets `[scheduler] create_cron_data_intervals = True`, in `airflow.cfg`, an env file, compose, a Dockerfile or Helm values, it's respected. That's the global way to keep Airflow 2's run dates.

### Migrate your repo

Add `.github/workflows/airflow-3.yml`:

```yaml
name: airflow-3-migration
on: workflow_dispatch

permissions:
  contents: write        # push the bot branch (never your default branch)
  pull-requests: write   # open / update the PR

jobs:
  migrate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/cache@v6     # keep the Airflow 3 test venv and Airflow 2 render venv between runs
        with:
          path: ${{ runner.temp }}/_github_home/aca-venv*
          key: aca-venvs-v1-airflow-3.3.2-${{ hashFiles('**/requirements*.txt', '**/pyproject.toml') }}
      - uses: patrickballard/airflow-migration-verifier@v1
        with:
          rules: airflow
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}   # optional: judgment sites
          max-llm-usd: "1"                                      # hard cap on Claude spend
          # dry-run: "true"                                     # report in the step summary only
```

1. Enable *Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests*. Leave the default permissions on *Read*: the workflow's `permissions:` block grants what it needs.
2. Run it: *Actions → airflow-3-migration → Run workflow*, or `gh workflow run airflow-3.yml`.

**What it does:**
- It raises your `apache-airflow` pin to 3.3.2.
- It builds an Airflow 3 venv with the official constraints, plus an Airflow 2 venv at your original pin for the render check.
- It pushes the branch `api-change-agent/airflow` and opens a PR whose description is the report.

**Without an Anthropic key,** judgment sites are listed for a human, and the report says so.

**Your other checks on its PR:** GitHub holds workflow runs on a PR opened by the job's token until you approve them. Open the PR and click *Approve and run* to run your CI and the verify workflow on it. To skip that, pass a personal access token or a GitHub App token as `github-token`.

**The cache:** later runs reuse both venvs while your requirements stay the same. On the fixture repo that brought a run down from 2m12s to 1m19s.

### Inputs

| Input | Default | |
|---|---|---|
| `mode` | `migrate` | `migrate` opens a PR; `verify` compares `before` with `after` and changes nothing. |
| `before`, `after` | | verify: the Airflow 2 and Airflow 3 trees, as paths in the workspace. |
| `strict` | `"false"` | verify: fail the step on a break (see above). |
| `rules` | `openai` | migrate: set it to `airflow`. Verify defaults to `airflow`. |
| `anthropic-api-key` | | migrate: enables Claude for judgment sites. |
| `max-llm-usd` | `"5"` | migrate: hard cap on Claude spend for the run. |
| `model` | `claude-opus-5-5` | migrate: the Claude model. |
| `max-llm-attempts` | `"2"` | migrate: attempts per file; each mismatch or test failure is fed back. |
| `dry-run` | `"false"` | migrate: report in the step summary only; no branch, no PR. |
| `branch`, `base-branch` | `api-change-agent/<package>`, the default branch | migrate: the bot branch and the PR's base. |
| `test-command`, `install-command` | `auto` | The DAG checks and how the test venv is installed. |
| `exclude` | | Globs to skip. |
| `venv-dir` | `/github/home/aca-venv` | Where the venvs go (the cache paths above). |
| `github-token` | `${{ github.token }}` | migrate: pushes the branch and opens the PR. |

**Naming:** the action was called *api-change-agent* until v1.3.0. Its Python package, CLI and bot branch keep that name.

### Run it locally

```bash
pip install "git+https://github.com/patrickballard/airflow-migration-verifier@v1"
python -m api_change_agent --repo . --rules airflow --dry-run --output out --install-command auto --venv /tmp/af3
python -m api_change_agent verify --before ../before --after . --install-command auto --output out
```

## Limitations

- **Python context reads aren't render-checked.** `context["execution_date"]` → `context["logical_date"]` is the documented equivalent, but only templates are rendered and compared. In Airflow 3 a manually triggered run can have no logical date; the PR lists each such site with a one-line fallback.
- **Some things are always manual:**
  - operators removed from their providers (`PostgresOperator`, `SnowflakeOperator`, `SubDagOperator`, …);
  - `prev_ds` / `next_ds` without a key;
  - schedules computed at runtime;
  - removed DAG methods.
- **Verification is only as good as what it can reach locally.**
  - A DAG that needs a connection, variable or private package just to import ends up unverified, and nothing in its file is checked.
  - `dag.test()` failing on connections or credentials, on both versions, isn't treated as a signal.
- **The render check samples runs; it doesn't cover every run.**
  - About 30 runs per DAG.
  - Fields that only resolve inside a running task (XCom) aren't compared.
  - 48 of the 121 headline DAGs have no template fields, so for them "renders identically" is vacuous. The DagBag import and the pin and `days_ago` proofs carry them.
  - DAGs with no schedule can't be render-checked: there are no scheduled runs to compare. They're reported as not render-checked.
- **Naive `start_date`s.** If the repo doesn't set `default_timezone`, pins assume UTC (Airflow's default) and the PR says so loudly.
- **The kwarg check needs to import the new class.** If its provider isn't installed in the test venv, the PR names the class and says its calls weren't checked.
- **GitHub Actions coverage.** The Airflow path has run end to end in GitHub Actions on a small fixture repo. A `@v1` dry run takes 2m12s with a cold cache and 1m19s with a warm one ([cold](https://github.com/patrickballard/api-change-agent-test/actions/runs/39999359662), [warm](https://github.com/patrickballard/api-change-agent-test/actions/runs/39999714583)). The [real PR](https://github.com/patrickballard/api-change-agent-test/pull/2) came from a run that wasn't a dry run. The 10 real repos above were run locally, not in Actions.

## FAQ

### Why not just set `create_cron_data_intervals = True`?

You can, and verify mode respects it when your repo sets it. But:
- **It has to be set before you upgrade.** Once a DAG has runs from Airflow 3's default `CronTriggerTimetable`, switching it to `CronDataIntervalTimetable` skips one scheduled run ([apache/airflow#66441](https://github.com/apache/airflow/pull/66441) documents this). The per-DAG pin this action writes follows the same rule: merge it together with the upgrade, not after it.
- **It only keeps the run dates.** `days_ago`, `execution_date` and the other removed context keys, removed DAG methods, and arguments Airflow 3 rejects all still break. The verifier checks those too.

### Does my code leave my CI?

No. The imports, `dag.test()` and the template rendering all run inside your GitHub Actions job, which downloads Airflow and your requirements from PyPI.
- **GitHub:** migrate mode uses the job's `GITHUB_TOKEN` to push its branch and open the PR in your repo.
- **Claude:** it's called only if you add your own `anthropic-api-key`. Then each file that has a judgment site is sent to Anthropic's API under your key, within `max-llm-usd`. Verify mode never calls Claude.

### How is this different from ruff, airflow-v2-to-v3, Astronomer's skill or pytest-airflow-in-a-box?

The others change your code or run your tests. This renders your templates on both versions and compares them, whoever made the change. [The competitor check](https://github.com/patrickballard/airflow-migration-verifier/blob/main/research/competitor-check/REPORT.md) ran each tool on the same 161 real DAGs and verified every output the same way:
- ruff alone, airflow-v2-to-v3 and airflow-migration-helper: 24–25 pass every check;
- this action: 120;
- Astronomer's skill with Claude Code, on 3 of the repos: 13 of 18 (this action: 15).

What they do better:
- **ruff** makes the import and rename fixes; this action runs it first and credits it.
- **[Astronomer's skill](https://github.com/astronomer/agents)** migrates the deployment too (image, compose services, Postgres, tests) and refactors operators this action leaves for a human.
- **[airflow-v2-to-v3](https://github.com/rajeshkbathula/airflow-v2-to-v3)** has argument renames this action lacks (`concurrency` → `max_active_tasks`, `fail_stop` → `fail_fast`).
- **[airflow-migration-helper](https://pypi.org/project/airflow-migration-helper/)** lints `airflow.cfg` against the new version, and checks dependencies and the DB migration.
- **[pytest-airflow-in-a-box](https://github.com/nredd/pytest-airflow-in-a-box)** is a full DAG-testing toolkit: trigger rules, branch skips, connection resolution. Its `airflow-migration-diff` runs your test suite on Airflow 2 and 3 and diffs the outcomes, so it finds what your tests cover. This action renders every template field without needing tests. The two work well together.

### What can't it check?

- **DAGs with no schedule** (`schedule=None`): there are no scheduled runs to compare, so they're reported as not render-checked.
- **Python context reads**, such as `context["execution_date"]` in a callable. They're rewritten where Airflow documents the same value, and listed, but not run and compared.
- **Runtime-only behaviour:** XCom values, what tasks do with connections and external systems, and DAGs that need a connection, variable or private package just to import. Those are reported as unverified.

More under [Limitations](#limitations).

## Also: OpenAI Python SDK rules

`rules: openai` (the action's default) migrates deprecated `openai` SDK usage the same way. There are three rules:
- `client.beta.chat.completions.parse/stream` → GA, deterministic;
- `functions=` → `tools=`, with Claude;
- declaring `httpx` once openai 3.x stops installing it.

Each comes with a pin bump and a test gate. See [`rules/openai.yaml`](https://github.com/patrickballard/airflow-migration-verifier/blob/main/rules/openai.yaml), [`research/openai-breaks/`](https://github.com/patrickballard/airflow-migration-verifier/tree/main/research/openai-breaks/) and [`research/real-world/SCORECARD.md`](https://github.com/patrickballard/airflow-migration-verifier/blob/main/research/real-world/SCORECARD.md).

## Development

CI (unit tests on Python 3.10–3.12, the real-Airflow integration tests and the render-check mutation tests) runs nightly from [a separate repo](https://github.com/patrickballard/api-change-agent-test/actions/workflows/ci.yml), because a Marketplace action's repo can't contain workflow files.

```bash
pip install -e '.[test]' && pytest
ACA_AIRFLOW2_VENV=.../airflow-2 ACA_AIRFLOW3_VENV=.../airflow-3 pytest   # + real-Airflow integration tests
```
