## Verdicts

5 clean, 4 need a tweak, 1 wrong (AMPATH). No render mismatch came from a change the agent made and
kept: every one is a DAG the agent left unpinned, or a template still using a removed variable.

- **dag.test()** (only DAGs whose tasks run locally on Airflow 2: 67 of 161): all 14 of them that
  import on Airflow 3 after the migration also pass `dag.test()` there. The other 53 don't import yet,
  52 of them because of `days_ago`.
- **Not counted as our failure:** `dag.test()` failing on connections, variables, credentials or the
  45s timeout, on both versions.

## What blocks the rest (ranked by DAGs)

| # | Gap | DAGs | Fix |
|---|---|---|---|
| 1 | `airflow.utils.dates.days_ago` removed; ruff has no fix | 70 (BasPH 60, made-ml 9, blockchain 1) | Deterministic rule: `days_ago(n)` → `pendulum.today("UTC").add(days=-n)`, the same instant (midnight UTC minus n days). The pin should then read its zone as UTC. |
| 2 | Cron pin skips DAGs whose `start_date` is in `default_args` (or comes from `days_ago`) | mozilla 43 sites, BasPH ~55, made-ml 4 | Read a module-level `default_args` dict literal. These went to Claude and mostly ran out of budget. |
| 3 | **An unpinned cron DAG shifts its dates silently.** The agent's own render check only covers pinned DAGs and changed fields, and the PR's manual entry says "budget used up", not "dates will move". | 5 mozilla DAGs caught by the full comparison (e.g. `--date=2024-10-06` → `2024-10-07`, weekly DAGs off by a week) | Render-check every cron DAG as a whole. When one stays unpinned, say in the PR that its run dates shift by one interval on Airflow 3. |
| 4 | ruff's unsafe operator swaps can be wrong: `MySqlOperator` → `SQLExecuteQueryOperator` keeps `mysql_conn_id=` | AMPATH (5 operators, 1 subclass) | The test gate can't see it, because those files fail before and after. Treat class swaps whose kwargs differ as manual, or re-check the swapped call's kwargs. |
| 5 | Removed provider classes (`PostgresOperator`, `SnowflakeOperator`, `BigQueryCreateEmptyTableOperator`, `SubDagOperator`) | 5 scored (more among excluded files) | Manual by nature; the PR should list them (today only the DAG table shows them). |
| 6 | Module-style imports (`from airflow.operators import bash_operator`), `provide_context=`, `task_concurrency=`, `apply_defaults` | ~6 | Small deterministic rules. |
| 7 | Claude's pins on naive `start_date`s assume `UTC` without the "Timezone assumed" warning | DaliGuerfali, BasPH, made-ml | Count Claude pins too in the warning. |

Cosmetic:
- The pin adds its own `from airflow.sdk import CronDataIntervalTimetable` line instead of merging it into ruff's `airflow.sdk` import.
- PR rows for added lines show "(not declared)" as the before text.

## Bugs this run found in the agent (fixed, separate commits, tests added)

- `8410f71` A function body containing `if`/`for`/`with` crashed the context-key scan.
- `0b8238b` The render check counted fields that only resolve in a running task (XCom) as mismatches. That reverted correct pins. TriggerDagRunOperator's `execution_date` → `logical_date` was never compared.
- `e370828`, `1694f7c` The DAG check and render didn't put the dags folder, `plugins/` (at the root or beside `dags/`) and the repo root on `sys.path`, as deployments do. One hanging `dag.test()` crashed the whole check.
- `1800ffd` ruff's AIR fixes left the old `from airflow.operators.dummy import DummyOperator` import in place, so the file still failed (47 DAGs).

## Runs and spend

- **Pass 1** ($1.75): results discarded. The checker bugs above and current-day provider versions broke the Airflow 2 baseline.
- **Pass 2** ($1.56): all 10 repos.
- **Pass 3** ($0.79): BasPH, made-ml, astronomer (after `1800ffd`) and DaliGuerfali (after `1694f7c`), replacing their pass-2 results.
- AMPATH and blockchain-etl weren't rerun after `1800ffd`; neither had a DummyOperator failure.
- **Budget:** $0.32 per repo. Each Claude call cost about $0.03–0.08, so repos with dozens of judgment sites ran out after 4–7 calls. Gaps 1 and 2 would remove most of those sites.

## Harness limits

- **Provider versions** come from the repo's era: its Airflow pin, else its image tag, else its last-commit date (`deps.json`). AMPATH's recent commits on old code picked providers that are too new, so 11 of its 12 files don't load on Airflow 2.
- **Excluded files:** 60 DAG files don't load on Airflow 2.11.2 with installable dependencies, for example `airflow.contrib` imports, missing private modules, or a connection read at parse time. They're listed below and excluded from the score.
- **Repo code ran locally** with a throwaway HOME and no credentials in its environment.
