# Competitor check: Airflow 2 → 3 migration tools (2026-10-24)

Every tool ran on the same 10 saved **original** repos: the scorecard's clones, at the commits in [`../airflow-real-world`](../airflow-real-world/SCORECARD.md). Each tool ran at its latest version, with its documented usage and no hand edits. Every output was then checked the same way, with `api-change-agent verify`, our tool's verify-only mode.
- **DAG import on Airflow 3.3.2.**
- **The removed-DAG-method and kwarg checks.**
- **The render check of every template field**, original on Airflow 2.11.2 against output on 3.3.2, runs paired by fire time.

Verify mode respects any `[scheduler] create_cron_data_intervals` / `create_delta_data_intervals` setting a tree makes. That's the legitimate global fix for the date shift. None of the outputs set it. Claude API spend: $0.

## Results: 10 repos, the 161 DAGs that load on Airflow 2.11.2 and fail on Airflow 3.3.2 as written

| Tool | Imports on Airflow 3 | Templates render-checked identical | No template fields | Render mismatch | Not render-checked (no schedule) | **Passes all checks** | Cron/timedelta schedules left to shift |
|---|---|---|---|---|---|---|---|
| ruff `--fix` alone (baseline) | 65 | 14 | 10 | 22 | 19 | **24** | 157 |
| airflow-v2-to-v3 0.1.0 | 67 | 14 | 11 | 21 | 21 | **25** | 157 |
| airflow-migration-helper 1.1.0, `--safe-only` (documented) | 65 | 14 | 10 | 23 | 18 | **24** | 157 |
| airflow-migration-helper 1.1.0, `--unsafe` | 68 | 14 | 10 | 23 | 21 | **24** | 157 |
| **api-change-agent** (deterministic only, $0) | **154** | **72** | 50 | **2** | 30 | **120** | **15** |

*Passes all checks* means: imports on Airflow 3, every compared template field renders identically on every sampled run (or the DAG has none), and it calls no DAG method Airflow 3 removed. DAGs with `schedule=None` can't be render-checked, because there are no scheduled runs to compare. They're counted as not proven, for every tool alike.

Our 120 is one fewer than the scorecard's 121. The scorecard also includes $0.60 of Claude patches; this run is deterministic only.

### Astronomer's skill, on 3 repos (18 of those DAGs)

| Tool | Imports | Identical | No fields | Mismatch | Not render-checked | **Passes all checks** |
|---|---|---|---|---|---|---|
| ruff alone | 7 | 0 | 2 | 4 | 1 | **2** |
| airflow-v2-to-v3 | 6 | 0 | 3 | 3 | 0 | **3** |
| airflow-migration-helper (either mode) | 7 | 0 | 2 | 4 | 1 | **2** |
| **Astronomer skill** + Claude Code | **18** | 6 | 7 | 3 | 2 | **13** |
| **api-change-agent** | 17 | 8 | 7 | **0** | 2 | **15** |

The repos were eliasbenaddou/personal_finance_de_project, made-ml-in-prod-2021/airflow-examples and DaliGuerfali/big-data. All three have cron DAGs and templates.

## Per tool

### ruff `--fix` alone (baseline)
- **Version and command:** ruff 0.17.2, `ruff check --preview --select AIR --fix --unsafe-fixes --no-cache --exit-zero .` in the repo root. That's the command the Astronomer skill and Airflow's upgrade guide document.
- **Why most DAGs still fail:**
  - It leaves the old import behind after swapping `DummyOperator` for `EmptyOperator`: `No module named 'airflow.operators.dummy'`, 45 DAGs.
  - It has no fix for `days_ago`: 37 DAGs.
- **Templates and schedules:** it doesn't touch templates and leaves every cron schedule on the new default.
- **Mismatch example** (DaliGuerfali `satellite_daily_pipeline`, `check_hdfs_data.op_kwargs`): for the run firing 2024-01-16 02:00 UTC, Airflow 2 rendered `{"date": "2024-01-15"}` and the output renders `{"date": "2024-01-16"}`. That shift is on every one of the 25 sampled runs.

### airflow-v2-to-v3 0.1.0 (PyPI, released 2026-04-01)
- **Command:** `airflow-v2-to-v3 migrate .`, in place, as its README documents. It's pure Python, with no network or subprocess calls; I read the code first.
- **How it works:** line-based regexes, with about 220 rules.
- **Why most DAGs still fail: its `days_ago` rewrite breaks code.**
  - `start_date=airflow.utils.dates.days_ago(3)` becomes `airflow.utils.dates.pendulum.today('UTC').add(days=-3)`, and it removes `import airflow.utils.dates`.
  - The result is `NameError: name 'airflow' is not defined` on 52 DAGs and `module 'airflow.utils.dates' has no attribute 'pendulum'` on 25.
  - `from airflow.utils.dates import days_ago` becomes `from pendulum import days_ago`.
  - The replacement it intends is correct: the same instant, not a fixed date. Its README's "`days_ago(1)` → `datetime(...)`" doesn't describe what the code does.
- **Templates:** it renames context keys only in `ctx['…']` / `.get('…')` lookups, never in Jinja templates, and leaves a `# TODO(airflow3)` comment where there's no replacement.
- **Schedules:** every cron schedule is left to shift.
- **Mismatch example** (DaliGuerfali `satellite_weekly_pipeline`, `calculate_week_range.op_kwargs`): Airflow 2 rendered `{"reference_date": "2024-01-14"}` and the output renders `"2024-01-21"`, for the run firing 2024-01-21 04:00 UTC. That's a week off, on all 21 runs.

### airflow-migration-helper 1.1.0 (PyPI, released 2026-10-16)
- **Command:** `migration-helper fix . --safe-only` (documented), and also `--unsafe`. It was installed with `[airflow]` (Airflow 3.2.1), with a throwaway `AIRFLOW_HOME`.
- **What `fix` does:** it applies ruff's AIR fixes, with ruff pinned to `<0.16` (0.15.22), and `airflow config update`. Its README says source-level DAG fixes outside ruff are "still planned".
- **Results:** they match ruff alone.
- **Exit code:** it exits 1 in every repo. That's because its own diagnostic stages couldn't run: `dag_check` needs a migrated metadata DB and `dep_check` a dependency resolver. It reports them as FAILED rather than as "0 issues", and they change nothing.
- **Mismatch example:** the same DaliGuerfali `satellite_monitoring` shift as ruff: `{"ds": "2024-01-15"}` → `"2024-01-16"`.

### Astronomer's `migrating-airflow-2-to-3` skill (astronomer/agents @ 486ee63, 2026-10-07)
- **How it ran:** Claude Code 2.1.306, `claude-opus-5-5`, in a fresh headless session per repo:
  - `claude -p "Migrate this repo to Airflow 3." --setting-sources project --strict-mcp-config --permission-mode acceptEdits --allowedTools …`, with Bash limited to read-only commands, ruff and py_compile;
  - the working directory was a temp copy of the original, with only the skill in `.claude/skills/`;
  - no CLAUDE.md, memory, user skills or MCP servers. The init event confirmed `apiKeySource: none`, so it ran on the logged-in plan, not the API.
- **Cost and time:** 214–265 s and 39–52 turns per repo. Notional cost was $1.07–1.37 per repo, from plan usage.
- **It saw the shift once:** in DaliGuerfali, it pinned the cron DAGs to `CronDataIntervalTimetable` itself, and explained correctly why `{{ ds }}` would move.
- **The shift elsewhere:** it didn't pin eliasbenaddou or made-ml, which explains its 3 mismatches.
  - Example (eliasbenaddou `monzo_transactions`, `trigger_dag.logical_date`): `'2024-01-15'` → `'2024-01-16'` on all 25 runs.
- **The skill's text names the global setting wrongly:** `AIRFLOW__SCHEDULER__CREATE_CRON_DATA_INTERVAL` (no S) and `AIRFLOW__CORE__CREATE_CRON_DATA_INTERVALS`. I tested both on Airflow 3.3.2, and **both are silently ignored**; only `AIRFLOW__SCHEDULER__CREATE_CRON_DATA_INTERVALS` works.
- **`days_ago` became a fixed date** in made-ml (`pendulum.datetime(2026, 10, 20, tz="UTC")`), which changes catchup and the first run.
- **It changed more than a migration needs**, none of it verified, since its sessions said they had no Airflow to run:
  - in eliasbenaddou it removed tasks (`get_monzo_auth`) and replaced `GreatExpectationsOperator` with a `@task`;
  - it also changed Dockerfiles, compose files, tests and docs.

### api-change-agent (ours)
- **Command:** `python -m api_change_agent --repo . --rules airflow --dry-run --test-command "python airflow_dag_check.py --repo . --junitxml {junit} --import-only --timeout 45" --venv <af3> --render-old-venv <af2> --max-llm-usd 0`, with no key.
- **What still fails:**
  - 3 SubDagOperator DAGs and 2 PostgresOperator DAGs (removed classes);
  - 1 `pytz` DAG, where the repo never declared the dependency;
  - 1 `days_ago(0, 2)` with a positional `hour`, which goes to judgment.
- **Its 2 mismatches** are real Airflow 3 breaks it reports instead of guessing:
  - `prev_ds` in BasPH;
  - `dag_run.external_trigger` in mozilla: Airflow 2 rendered `'default'`, Airflow 3 errors because DagRun no longer has the attribute.
- **Schedules:** 15 left to shift, all listed in the PR as "Run dates will shift". Each needs judgment:
  - 11 are timedelta schedules;
  - 4 are cron schedules whose timezone can't be read off the code, such as AMPATH's `start_date` with `tzinfo=TIMEZONE`.

## What the competitors do better than us

- **Astronomer skill + Claude:** the broadest scope.
  - It migrates the deployment too: the image tag, compose services (`api-server`, `dag-processor`), Postgres version, requirements and tests.
  - It fixed one DAG we leave manual (the Great Expectations operator, refactored into a `@task`), and so imports 18 of 18 to our 17 on its repos.
  - It handles manual runs with `run_after`, and explains its risks in prose.
  - When it does pin, it explains why.
- **airflow-v2-to-v3:** several kwarg renames and removals we don't have:
  - `concurrency` → `max_active_tasks`;
  - `fail_stop` → `fail_fast`;
  - dropping `default_view`, `orientation` and `sla_miss_callback`.
  - It also renames trigger rules (`DUMMY` → `ALWAYS`) and dataset → asset methods, flags local folders that shadow Airflow modules (`utils/`, `models/`), and is fast and simple.
- **airflow-migration-helper:** checks we don't do.
  - `airflow config lint` / `update` against the target version, a dependency-compatibility check and a DB-migration check.
  - Honest checker status: a checker that couldn't run is never shown as "0 issues".
  - JSON and HTML reports.

## Fairness and limits

- **The yardstick is ours.** Verify mode runs our own checks, so this isn't a neutral judge, though every tool, ours included, went through the same command. The checks follow Airflow's own behaviour, and are backed by the render check's mutation suite (`tests/test_render_mutations.py`). A wrong migration reads as a mismatch or "not render-checked", never as identical.
- **DAG checks were import-only** (`--import-only`) for everyone, with no `dag.test()`.
- **The Astronomer skill ran on 3 repos only.** Its sessions couldn't install or run Airflow, so it worked blind. A user with a running Airflow could catch its mismatches.
- **Repo setup, not tool quality:** 60 DAG files that don't load on Airflow 2.11.2 are outside the 161, as in the scorecard. The "unverified files" counts in `results.json` include them.
- **Two bugs in our tool, found during this check and fixed in separate commits before the final numbers:**
  - `991f9e9`: `--rules airflow` crashed in repos that have an `airflow/` folder.
  - `19f1256`: for weekly and monthly crons, the render sampler snapped the two timetable types to different runs, which produced false mismatches. The first pass of these verifies was discarded and rerun.

Files: [`run_tools.py`](run_tools.py) (transforms, verifies, table), [`run_astronomer_skill.py`](run_astronomer_skill.py), [`results.json`](results.json). Trees and logs stay local in `~/SDK/competitor-check`, since they hold other people's code.
