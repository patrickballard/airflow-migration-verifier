# api-change-agent: Airflow 2 → 3, real-world dry run

10 public repos with Airflow 2 DAGs, cloned locally (`~/SDK/real-world-airflow`, not committed). Each ran `--dry-run` with the render check on: Airflow 2.11.2 vs 3.3.2, Python 3.12, official constraints plus each repo's declared and imported providers. Nothing was pushed and no PRs were opened. Scripts: `find_candidates.py`, `run_repo.py`, `run_all.py`, `scorecard.py`.

**Headline: 60 of 161 DAGs (37%) went from failing on Airflow 3 to importing on Airflow 3 with templates rendering identically to Airflow 2.**

Counted over DAGs that load on Airflow 2.11.2. 3 more already imported on Airflow 3 before the migration; 2 of them still import and render identically after it. Claude spend: $1.61 for the runs in this table; $4.10 in all, counting the runs they replaced (see Runs below), within the $5.00 limit.

| Repo | DAGs | AF3 import before → after | Pins | Render check | Manual items | Time | Claude | Verdict |
|---|---|---|---|---|---|---|---|---|
| [eliasbenaddou/personal_finance_de_project](https://github.com/eliasbenaddou/personal_finance_de_project/tree/e34718b) | 4 (+1 files excluded) | 0/4 → 4/4 | 3 | 4/4 DAGs identical (4 fields) | 0 | 1m40s | $0.00 | **clean** pins proven identical; TriggerDagRun execution_date -> logical_date render-checked; UTC assumption flagged |
| [mozilla/telemetry-airflow](https://github.com/mozilla/telemetry-airflow/tree/cf609dd) | 51 (+5 files excluded) | 3/51 → 46/51 | 3 + 2 by Claude | 41/46 DAGs identical (708 fields, 36 mismatched) | 59: needs Claude (budget) ×44; ruff AIR301: no automatic fix ×7; ruff AIR311: no automatic fix ×5; ruff AIR304: no automatic fix ×3 | 46m47s | $0.26 | **needs tweak** 46/51 import, but 5 cron DAGs left unpinned (start_date in default_args) shift dates by one interval, and the PR doesn't say so |
| [BasPH/data-pipelines-with-apache-airflow](https://github.com/BasPH/data-pipelines-with-apache-airflow/tree/d5b18a7) | 87 (+15 files excluded) | 0/87 → 16/87 | 19 + 8 by Claude | 12/16 DAGs identical (3 fields, 2 mismatched) | 172: ruff AIR301: no automatic fix ×90; needs Claude (budget) ×80; ruff AIR311: no automatic fix ×2 | 36m47s | $0.25 | **needs tweak** no wrong changes found; 60 DAGs still blocked on days_ago (no fix); 2 fields still use prev_ds (Claude budget) |
| [sanchitvj/sports_betting_analytics_engine](https://github.com/sanchitvj/sports_betting_analytics_engine/tree/855a744) | 1 (+7 files excluded) | 0/1 → 1/1 | 3 | 1/1 DAGs identical (20 fields) | 10: ruff AIR301: no automatic fix ×10 | 1m12s | $0.00 | **clean** 20 fields identical incl. 5 TriggerDagRun execution_date -> logical_date |
| [anilkulkarni87/airflow-docker](https://github.com/anilkulkarni87/airflow-docker/tree/8b87bef) | 3 (+3 files excluded) | 0/3 → 1/3 | 0 | 1/1 DAGs identical (0 fields) | 3: ruff AIR301: no automatic fix ×3 | 3m02s | $0.00 | **clean** partial: the other 2 DAGs use PostgresOperator, removed from the provider (no automatic fix) |
| [astronomer/airflow-data-quality-demo](https://github.com/astronomer/airflow-data-quality-demo/tree/8847b1c) | 2 (+16 files excluded) | 0/2 → 0/2 | 0 | 0/0 DAGs identical (0 fields) | 1: ruff AIR303: no automatic fix ×1 | 2m54s | $0.00 | **clean** nothing wrong, but neither DAG imports: SnowflakeOperator / PostgresOperator removed from their providers, and the PR doesn't list them as manual |
| [blockchain-etl/bitcoin-etl-airflow](https://github.com/blockchain-etl/bitcoin-etl-airflow/tree/72a88b0) | 1 (+2 files excluded) | 0/1 → 0/1 | 0 + 1 by Claude | 0/0 DAGs identical (0 fields) | 10: ruff AIR301: no automatic fix ×7; needs Claude (budget) ×2; ruff AIR302: no automatic fix ×1 | 2m24s | $0.32 | **needs tweak** module-style `from airflow.operators import bash_operator` kept (ruff can't fix it); Claude's schedule helper is sound but unverified |
| [DaliGuerfali/big-data](https://github.com/DaliGuerfali/big-data/tree/df84c1f) | 3 | 0/3 → 3/3 | 0 + 3 by Claude | 3/3 DAGs identical (8 fields) | 3: needs Claude (budget) ×3 | 13m34s | $0.28 | **clean** 3/3 import, 8 fields identical; Claude's pins assume UTC without the UTC warning |
| [AMPATH/etl](https://github.com/AMPATH/etl/tree/f932b01) | 1 (+11 files excluded) | 0/1 → 0/1 | 0 + 3 by Claude | 0/0 DAGs identical (0 fields) | 7: ruff AIR301: no automatic fix ×4; needs Claude (budget) ×3 | 2m49s | $0.24 | **wrong** ruff's unsafe AIR302 fix turns MySqlOperator into SQLExecuteQueryOperator but keeps mysql_conn_id=, which Airflow 3 rejects |
| [made-ml-in-prod-2021/airflow-examples](https://github.com/made-ml-in-prod-2021/airflow-examples/tree/96aaf7b) | 11 | 0/11 → 0/11 | 0 + 7 by Claude | 0/0 DAGs identical (0 fields) | 15: ruff AIR301: no automatic fix ×11; needs Claude (budget) ×4 | 7m35s | $0.25 | **needs tweak** 0/11: all blocked on days_ago / provide_context (no fix); Claude's pins are reasonable |

Pins: cron/delta schedules pinned to Airflow 2's timetable, deterministic + by Claude. Render check: DAGs that import after the migration, every template field compared. Time is wall-clock for the whole repo (venvs, Airflow 2 baseline, agent, full render); Claude is that repo's spend.

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

## Excluded: DAG files that don't load on Airflow 2.11.2

- `eliasbenaddou/personal_finance_de_project` `dags/monzo_dq_checks.py`: ModuleNotFoundError: No module named 'great_expectations_provider.operators.great_expectations'
- `mozilla/telemetry-airflow` `dags/adm_dma_export.py`: airflow.exceptions.AirflowNotFoundException: The conn_id `adm_sftp` isn't defined
- `mozilla/telemetry-airflow` `dags/adm_export.py`: airflow.exceptions.AirflowNotFoundException: The conn_id `adm_sftp` isn't defined
- `mozilla/telemetry-airflow` `dags/looker.py`: KeyError: 'Variable lookml_generator_release_str does not exist'
- `mozilla/telemetry-airflow` `dags/merino_jobs.py`: airflow.exceptions.AirflowNotFoundException: The conn_id `merino_elasticsearch_prod` isn't defined
- `mozilla/telemetry-airflow` `dags/probe_scraper.py`: KeyError: 'Variable glean_dictionary_netlify_build_webhook_id does not exist'
- `BasPH/data-pipelines-with-apache-airflow` `chapter04/dags/listing_4_20.py`: ModuleNotFoundError: No module named 'airflow.providers.postgres.operators'
- `BasPH/data-pipelines-with-apache-airflow` `chapter07/digit_classifier/dags/chapter9_digit_classifier.py`: ModuleNotFoundError: No module named 'airflow.providers.amazon.aws.operators.s3_copy_object'
- `BasPH/data-pipelines-with-apache-airflow` `chapter07/insideairbnb/dags/chapter7_insideairbnb.py`: ValueError: Invalid `auto_remove` value True, expected one of 'never', 'success', or 'force'.
- `BasPH/data-pipelines-with-apache-airflow` `chapter08/dags/01_python.py`: KeyError: 'MOVIELENS_USER'
- `BasPH/data-pipelines-with-apache-airflow` `chapter09/dags/bash_operator_no_command.py`: airflow.exceptions.AirflowException: missing keyword argument 'bash_command'
- `BasPH/data-pipelines-with-apache-airflow` `chapter09/dags/dag_cycle.py`: AirflowDagCycleException: Cycle detected in DAG: chapter8_dag_cycle. Faulty task: t3
- `BasPH/data-pipelines-with-apache-airflow` `chapter09/dags/duplicate_task_ids.py`: airflow.exceptions.DuplicateTaskIdFound: Task id 'task' has already been added to the DAG
- `BasPH/data-pipelines-with-apache-airflow` `chapter09/dags/testme.py`: airflow.exceptions.DuplicateTaskIdFound: Task id 'test2' has already been added to the DAG
- `BasPH/data-pipelines-with-apache-airflow` `chapter10/docker/dags/01_docker.py`: KeyError: 'MOVIELENS_USER'
- `BasPH/data-pipelines-with-apache-airflow` `chapter10/kubernetes/dags/02_kubernetes.py`: ModuleNotFoundError: No module named 'airflow.providers.cncf.kubernetes.operators.kubernetes_pod'
- `BasPH/data-pipelines-with-apache-airflow` `chapter13/secretsbackend/dag.py`: ImportError: cannot import name 'SimpleHttpOperator' from 'airflow.providers.http.operators.http' (/Users/paddy/SDK/real-world-airflow/.venvs/BasPH__data-pipeli
- `BasPH/data-pipelines-with-apache-airflow` `chapter14/dags/nyc_dag.py`: ModuleNotFoundError: No module named 'nyctransport'
- `BasPH/data-pipelines-with-apache-airflow` `chapter16/dags/01_aws_usecase.py`: ImportError: cannot import name 'AWSAthenaOperator' from 'airflow.providers.amazon.aws.operators.athena' (/Users/paddy/SDK/real-world-airflow/.venvs/BasPH__data
- `BasPH/data-pipelines-with-apache-airflow` `chapter17/dags/01_azure_usecase.py`: Reason: tried: '/opt/homebrew/opt/unixodbc/lib/libodbc.2.dylib' (no such file), '/System/Volumes/Preboot/Cryptexes/OS/opt/homebrew/opt/unixodbc/lib/libodbc.2.dy
- `BasPH/data-pipelines-with-apache-airflow` `chapter18/dags/gcp.py`: ImportError: cannot import name 'BigQueryExecuteQueryOperator' from 'airflow.providers.google.cloud.operators.bigquery' (/Users/paddy/SDK/real-world-airflow/.ve
- `sanchitvj/sports_betting_analytics_engine` `dags/odds_batch_processing.py`: ImportError: cannot import name 'NoBrokersAvailable' from 'kafka.errors' (/Users/paddy/SDK/real-world-airflow/.venvs/sanchitvj__sports_betting_analytics_engine-
- `sanchitvj/sports_betting_analytics_engine` `dags/odds_ingestion_dqc.py`: ImportError: cannot import name 'NoBrokersAvailable' from 'kafka.errors' (/Users/paddy/SDK/real-world-airflow/.venvs/sanchitvj__sports_betting_analytics_engine-
- `sanchitvj/sports_betting_analytics_engine` `dags/snowflake_dbt_staging_marts.py`: ImportError: cannot import name 'SnowflakeOperator' from 'airflow.providers.snowflake.operators.snowflake' (/Users/paddy/SDK/real-world-airflow/.venvs/sanchitvj
- `sanchitvj/sports_betting_analytics_engine` `dags/sports_batch_backup.py`: ImportError: cannot import name 'NoBrokersAvailable' from 'kafka.errors' (/Users/paddy/SDK/real-world-airflow/.venvs/sanchitvj__sports_betting_analytics_engine-
- `sanchitvj/sports_betting_analytics_engine` `dags/sports_batch_processing.py`: ImportError: cannot import name 'NoBrokersAvailable' from 'kafka.errors' (/Users/paddy/SDK/real-world-airflow/.venvs/sanchitvj__sports_betting_analytics_engine-
- `sanchitvj/sports_betting_analytics_engine` `dags/sports_ingestion_backup.py`: ImportError: cannot import name 'NoBrokersAvailable' from 'kafka.errors' (/Users/paddy/SDK/real-world-airflow/.venvs/sanchitvj__sports_betting_analytics_engine-
- `sanchitvj/sports_betting_analytics_engine` `dags/sports_ingestion_dqc.py`: ImportError: cannot import name 'NoBrokersAvailable' from 'kafka.errors' (/Users/paddy/SDK/real-world-airflow/.venvs/sanchitvj__sports_betting_analytics_engine-
- `anilkulkarni87/airflow-docker` `dags/covidNyDaily.py`: airflow.exceptions.AirflowNotFoundException: The conn_id `postgres_new` isn't defined
- `anilkulkarni87/airflow-docker` `dags/covidNyInitial.py`: airflow.exceptions.AirflowNotFoundException: The conn_id `postgres_new` isn't defined
- `anilkulkarni87/airflow-docker` `dags/test_minio_local.py`: ImportError: Could not import `airflow.providers.amazon.aws.hooks.s3.S3Hook` while trying to import `airflow.hooks.S3_hook.S3Hook`.
- `astronomer/airflow-data-quality-demo` `dags/bigquery_examples/simple_bigquery.py`: ImportError: cannot import name 'BigQueryCreateEmptyTableOperator' from 'airflow.providers.google.cloud.operators.bigquery' (/Users/paddy/SDK/real-world-airflow
- `astronomer/airflow-data-quality-demo` `dags/firebolt_examples/simple_firebolt.py`: ModuleNotFoundError: No module named 'firebolt_provider'
- `astronomer/airflow-data-quality-demo` `dags/great_expectations/great_expectations_bigquery.py`: ImportError: cannot import name 'BigQueryCreateEmptyTableOperator' from 'airflow.providers.google.cloud.operators.bigquery' (/Users/paddy/SDK/real-world-airflow
- `astronomer/airflow-data-quality-demo` `dags/great_expectations/great_expectations_mlflow.py`: Reason: tried: '/opt/homebrew/opt/libomp/lib/libomp.dylib' (no such file), '/System/Volumes/Preboot/Cryptexes/OS/opt/homebrew/opt/libomp/lib/libomp.dylib' (no s
- `astronomer/airflow-data-quality-demo` `dags/great_expectations/great_expectations_pandas_df.py`: ModuleNotFoundError: No module named 'great_expectations_provider.operators.great_expectations'
- `astronomer/airflow-data-quality-demo` `dags/great_expectations/great_expectations_redshift.py`: ModuleNotFoundError: No module named 'airflow.providers.amazon.aws.operators.redshift_sql'
- `astronomer/airflow-data-quality-demo` `dags/great_expectations/great_expectations_snowflake.py`: ImportError: cannot import name 'SnowflakeOperator' from 'airflow.providers.snowflake.operators.snowflake' (/Users/paddy/SDK/real-world-airflow/.venvs/astronome
- `astronomer/airflow-data-quality-demo` `dags/great_expectations/great_expectations_snowflake_write_audit_publish.py`: ImportError: cannot import name 'SnowflakeOperator' from 'airflow.providers.snowflake.operators.snowflake' (/Users/paddy/SDK/real-world-airflow/.venvs/astronome
- `astronomer/airflow-data-quality-demo` `dags/redshift_examples/simple_redshift_2.py`: ModuleNotFoundError: No module named 'airflow.providers.postgres.operators'
- `astronomer/airflow-data-quality-demo` `dags/redshift_examples/simple_redshift_3.py`: ModuleNotFoundError: No module named 'airflow.providers.postgres.operators'
- `astronomer/airflow-data-quality-demo` `dags/snowflake_examples/complex_snowflake_transform.py`: ImportError: cannot import name 'SnowflakeOperator' from 'airflow.providers.snowflake.operators.snowflake' (/Users/paddy/SDK/real-world-airflow/.venvs/astronome
- `astronomer/airflow-data-quality-demo` `dags/snowflake_examples/simple_snowflake.py`: ImportError: cannot import name 'SnowflakeOperator' from 'airflow.providers.snowflake.operators.snowflake' (/Users/paddy/SDK/real-world-airflow/.venvs/astronome
- `astronomer/airflow-data-quality-demo` `dags/snowflake_examples/snowflake_dynamic_write_audit_publish.py`: ImportError: cannot import name 'SnowflakeOperator' from 'airflow.providers.snowflake.operators.snowflake' (/Users/paddy/SDK/real-world-airflow/.venvs/astronome
- `astronomer/airflow-data-quality-demo` `dags/snowflake_examples/snowflake_write_audit_publish.py`: ImportError: cannot import name 'SnowflakeOperator' from 'airflow.providers.snowflake.operators.snowflake' (/Users/paddy/SDK/real-world-airflow/.venvs/astronome
- `astronomer/airflow-data-quality-demo` `dags/snowflake_examples/taxi_snowflake.py`: ImportError: cannot import name 'SnowflakeOperator' from 'airflow.providers.snowflake.operators.snowflake' (/Users/paddy/SDK/real-world-airflow/.venvs/astronome
- `astronomer/airflow-data-quality-demo` `dags/sql_examples/sql_check_redshift_etl.py`: ModuleNotFoundError: No module named 'airflow.providers.postgres.operators'
- `blockchain-etl/bitcoin-etl-airflow` `dags/bitcoinetl/build_load_dag.py`: ImportError: Could not import `airflow.providers.google.cloud.operators.bigquery.BigQueryExecuteQueryOperator` while trying to import `airflow.contrib.operators
- `blockchain-etl/bitcoin-etl-airflow` `dags/bitcoinetl/build_verify_streaming_dag.py`: ImportError: Could not import `airflow.providers.google.cloud.operators.bigquery.BigQueryExecuteQueryOperator` while trying to import `airflow.contrib.operators
- `AMPATH/etl` `airflow/dags/all_etl_jobs_realtime.py`: ImportError: Could not import `airflow.providers.mysql.operators.mysql.MySqlOperator` while trying to import `airflow.operators.mysql_operator.MySqlOperator`.
- `AMPATH/etl` `airflow/dags/amrs_garbage_collection.py`: ImportError: cannot import name 'SimpleHttpOperator' from 'airflow.operators.http_operator' (unknown location)
- `AMPATH/etl` `airflow/dags/base_tables.py`: ImportError: Could not import `airflow.providers.mysql.operators.mysql.MySqlOperator` while trying to import `airflow.operators.mysql_operator.MySqlOperator`.
- `AMPATH/etl` `airflow/dags/build_hiv_summaries.py`: ImportError: Could not import `airflow.providers.mysql.operators.mysql.MySqlOperator` while trying to import `airflow.operators.mysql_operator.MySqlOperator`.
- `AMPATH/etl` `airflow/dags/eid_sync_job_daily.py`: ImportError: cannot import name 'SSHHook' from 'airflow.contrib.hooks' (/Users/paddy/SDK/real-world-airflow/.venvs/AMPATH__etl-af2/lib/python3.12/site-packages/
- `AMPATH/etl` `airflow/dags/eid_sync_job_weekly.py`: ImportError: Could not import `airflow.providers.ssh.operators.ssh.SSHOperator` while trying to import `airflow.contrib.operators.ssh_operator.SSHOperator`.
- `AMPATH/etl` `airflow/dags/hiv_monthly_summary_nightly.py`: ImportError: Could not import `airflow.providers.mysql.operators.mysql.MySqlOperator` while trying to import `airflow.operators.mysql_operator.MySqlOperator`.
- `AMPATH/etl` `airflow/dags/nightly_jobs.py`: ImportError: Could not import `airflow.providers.mysql.operators.mysql.MySqlOperator` while trying to import `airflow.operators.mysql_operator.MySqlOperator`.
- `AMPATH/etl` `airflow/dags/surge_daily.py`: ImportError: Could not import `airflow.providers.mysql.operators.mysql.MySqlOperator` while trying to import `airflow.operators.mysql_operator.MySqlOperator`.
- `AMPATH/etl` `airflow/dags/sync_hiv_summary_only.py`: ImportError: Could not import `airflow.providers.mysql.operators.mysql.MySqlOperator` while trying to import `airflow.operators.mysql_operator.MySqlOperator`.
- `AMPATH/etl` `airflow/dags/test_new_etl_scripts.py`: ImportError: Could not import `airflow.providers.mysql.operators.mysql.MySqlOperator` while trying to import `airflow.operators.mysql_operator.MySqlOperator`.

## Render mismatches (full comparison)

- `mozilla/telemetry-airflow` fxci_metric_export.fxci_metric_export.arguments: render mismatch on 3 of 3 runs, e.g. the run firing 2024-10-07 00:30 UTC (from the ordinary 6 sample): Airflow 2 rendered '["fxci-etl", "metric", "export", "-vv", "--date=2024-10-06"]', the new template renders '["fxci-etl", "metric", "export", "-vv",
- `mozilla/telemetry-airflow` glam_fog_release.firefox_desktop_glam_release__scalar_bucket_counts_v1.query_firefox_desktop_glam_release__scalar_bucket_counts_v1_sampled_0_19.env_vars: render mismatch on 2 of 2 runs, e.g. the run firing 2024-12-28 10:00 UTC (from the ordinary 19 sample): Airflow 2 rendered '["{\'name\': \'PRODUCT
- `mozilla/telemetry-airflow` glam_fog_release.firefox_desktop_glam_release__scalar_bucket_counts_v1.query_firefox_desktop_glam_release__scalar_bucket_counts_v1_sampled_20_39.env_vars: render mismatch on 2 of 2 runs, e.g. the run firing 2024-12-28 10:00 UTC (from the ordinary 19 sample): Airflow 2 rendered '["{\'name\': \'PRODUC
- `mozilla/telemetry-airflow` glam_fog_release.firefox_desktop_glam_release__scalar_bucket_counts_v1.query_firefox_desktop_glam_release__scalar_bucket_counts_v1_sampled_40_59.env_vars: render mismatch on 2 of 2 runs, e.g. the run firing 2024-12-28 10:00 UTC (from the ordinary 19 sample): Airflow 2 rendered '["{\'name\': \'PRODUC
- `mozilla/telemetry-airflow` glam_fog_release.firefox_desktop_glam_release__scalar_bucket_counts_v1.query_firefox_desktop_glam_release__scalar_bucket_counts_v1_sampled_60_79.env_vars: render mismatch on 2 of 2 runs, e.g. the run firing 2024-12-28 10:00 UTC (from the ordinary 19 sample): Airflow 2 rendered '["{\'name\': \'PRODUC
- `mozilla/telemetry-airflow` glam_fog_release.firefox_desktop_glam_release__scalar_bucket_counts_v1.query_firefox_desktop_glam_release__scalar_bucket_counts_v1_sampled_80_99.env_vars: render mismatch on 2 of 2 runs, e.g. the run firing 2024-12-28 10:00 UTC (from the ordinary 19 sample): Airflow 2 rendered '["{\'name\': \'PRODUC
- `mozilla/telemetry-airflow` glam_fog_release.query_firefox_desktop_glam_release__scalar_probe_counts_v1.env_vars: render mismatch on 2 of 2 runs, e.g. the run firing 2024-12-28 10:00 UTC (from the ordinary 19 sample): Airflow 2 rendered '["{\'name\': \'PRODUCT\', \'value\': \'firefox_desktop_glam_release\', \'value_from\': Non
- `mozilla/telemetry-airflow` glam_fog_release.firefox_desktop_glam_release__histogram_bucket_counts_v1.query_firefox_desktop_glam_release__histogram_bucket_counts_v1_sampled_0_0.env_vars: render mismatch on 2 of 2 runs, e.g. the run firing 2024-12-28 10:00 UTC (from the ordinary 19 sample): Airflow 2 rendered '["{\'name\': \'PR
- `BasPH/data-pipelines-with-apache-airflow` chapter7_insideairbnb.download_from_postgres._query: render mismatch on 10 of 10 runs, e.g. the run firing 2016-03-01 00:00 UTC (from the 23:00 sample): Airflow 2 rendered "SELECT * FROM listings WHERE download_date BETWEEN '2016-01-01' AND '2016-02-01'", the new template renders "RENDER ERROR Undef
- `BasPH/data-pipelines-with-apache-airflow` chapter7_insideairbnb.download_from_postgres._s3_key: render mismatch on 10 of 10 runs, e.g. the run firing 2016-03-01 00:00 UTC (from the 23:00 sample): Airflow 2 rendered 'listing-2016-02-01.csv', the new template renders "RENDER ERROR UndefinedError: 'prev_ds' is undefined"
