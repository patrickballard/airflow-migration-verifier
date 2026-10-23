# api-change-agent: Airflow 2 → 3, real-world dry run

10 public repos with Airflow 2 DAGs, cloned locally (`~/SDK/real-world-airflow`, not committed). Each ran `--dry-run` with the render check on: Airflow 2.11.2 vs 3.3.2, Python 3.12, official constraints plus each repo's declared and imported providers. Nothing was pushed and no PRs were opened. Scripts: `find_candidates.py`, `run_repo.py`, `run_all.py`, `scorecard.py`.

**Headline: 144 of 161 DAGs (89%) went from failing on Airflow 3 to importing on Airflow 3 with templates rendering identically to Airflow 2, and call no DAG method Airflow 3 removed.**

Same measure at earlier stages:
- before this round (previous scorecard): 60 of 161 (37%)
- deterministic rules only, no Claude: 143 of 161 (89%)

Counted over DAGs that load on Airflow 2.11.2. 3 more already imported on Airflow 3 before the migration; 2 of them still import and render identically after it. Claude spend: $0.60 for the runs in this table; $4.70 in all, counting the runs they replaced (see Runs below), within the $5.00 limit.

| Repo | DAGs | AF3 import before → after | Pins | Render check | Unpinned / unverified | Manual items | Time | Claude | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| [eliasbenaddou/personal_finance_de_project](https://github.com/eliasbenaddou/personal_finance_de_project/tree/e34718b) | 4 (+1 files excluded) | 0/4 → 4/4 | 3 | 4/4 DAGs identical (4 fields) | 0 / 1 | 1: ruff AIR311: unsafe fix withheld (unverified file) ×1 | 1m31s | $0.00 | **clean** pins proven identical; TriggerDagRun execution_date -> logical_date render-checked; UTC assumption flagged |
| [mozilla/telemetry-airflow](https://github.com/mozilla/telemetry-airflow/tree/cf609dd) | 51 (+5 files excluded) | 3/51 → 46/51 | 46 + 2 by Claude | 45/46 DAGs identical (708 fields) | 0 / 8 | 66: ruff AIR312: unsafe fix withheld (unverified file) ×21; ruff AIR311: unsafe fix withheld (unverified file) ×16; ruff AIR321: unsafe fix withheld (unverified file) ×13; ruff AIR301: no automatic fix ×7 | 24m43s | $0.09 | **clean** default_args start dates now pin (46 pins, 2 by Claude); 45/46 identical, shredder didn't render on one side; SubDagOperator etc. manual |
| [BasPH/data-pipelines-with-apache-airflow](https://github.com/BasPH/data-pipelines-with-apache-airflow/tree/d5b18a7) | 87 (+15 files excluded) | 0/87 → 87/87 | 77 + 3 by Claude | 83/87 DAGs identical (113 fields, 2 mismatched) | 0 / 15 | 61: needs Claude (budget) ×20; ruff AIR311: unsafe fix withheld (unverified file) ×15; ruff AIR312: unsafe fix withheld (unverified file) ×14; judgment, not attempted (unverified file) ×5 | 97m04s | $0.18 | **needs tweak** 152 days_ago rewrites + 77 pins, 87/87 import; but 2 DAGs call the removed DAG.following_schedule, which nothing flags |
| [sanchitvj/sports_betting_analytics_engine](https://github.com/sanchitvj/sports_betting_analytics_engine/tree/855a744) | 1 (+7 files excluded) | 0/1 → 1/1 | 3 | 1/1 DAGs identical (20 fields) | 0 / 7 | 50: ruff AIR312: unsafe fix withheld (unverified file) ×23; ruff AIR311: unsafe fix withheld (unverified file) ×10; ruff AIR301: no automatic fix ×10; ruff AIR321: unsafe fix withheld (unverified file) ×7 | 1m00s | $0.00 | **clean** 20 fields identical incl. 5 TriggerDagRun execution_date -> logical_date |
| [anilkulkarni87/airflow-docker](https://github.com/anilkulkarni87/airflow-docker/tree/8b87bef) | 3 (+3 files excluded) | 0/3 → 1/3 | 0 | 1/1 DAGs identical (0 fields) | 0 / 5 | 12: ruff AIR311: unsafe fix withheld (unverified file) ×6; ruff AIR302: unsafe fix withheld (unverified file) ×2; ruff AIR301: unsafe fix withheld (unverified file) ×1; ruff AIR321: unsafe fix withheld (unverified file) ×1 | 2m10s | $0.00 | **clean** partial: the other 2 DAGs use PostgresOperator, removed from the provider |
| [astronomer/airflow-data-quality-demo](https://github.com/astronomer/airflow-data-quality-demo/tree/8847b1c) | 2 (+16 files excluded) | 0/2 → 0/2 | 0 | 0/0 DAGs identical (0 fields) | 0 / 18 | 91: ruff AIR311: unsafe fix withheld (unverified file) ×53; ruff AIR312: unsafe fix withheld (unverified file) ×13; ruff AIR321: unsafe fix withheld (unverified file) ×12; ruff AIR302: unsafe fix withheld (unverified file) ×12 | 2m13s | $0.00 | **clean** neither DAG imports (removed provider operators); every such file now listed as unverified, nothing presented as done |
| [blockchain-etl/bitcoin-etl-airflow](https://github.com/blockchain-etl/bitcoin-etl-airflow/tree/72a88b0) | 1 (+2 files excluded) | 0/1 → 1/1 | 0 + 1 by Claude | 1/1 DAGs identical (0 fields) | 3 / 3 | 17: ruff AIR301: no automatic fix ×6; ruff AIR302: unsafe fix withheld (unverified file) ×4; ruff AIR311: unsafe fix withheld (unverified file) ×3; judgment, not attempted (unverified file) ×3 | 1m51s | $0.11 | **clean** 1/1 imports; 3 computed schedules in build_export_dag now flagged under Run dates will shift |
| [DaliGuerfali/big-data](https://github.com/DaliGuerfali/big-data/tree/df84c1f) | 3 | 0/3 → 3/3 | 3 | 3/3 DAGs identical (8 fields) | 0 / 0 | 3: needs Claude (budget) ×3 | 3m09s | $0.09 | **clean** 3/3 import, 8 fields identical; Claude judged the 3 context sites false positives and left them |
| [AMPATH/etl](https://github.com/AMPATH/etl/tree/f932b01) | 1 (+11 files excluded) | 0/1 → 0/1 | 3 | 0/0 DAGs identical (0 fields) | 3 / 12 | 46: ruff AIR302: unsafe fix withheld (unverified file) ×27; ruff AIR311: unsafe fix withheld (unverified file) ×12; ruff AIR301: no automatic fix ×4; judgment, not attempted (unverified file) ×3 | 1m43s | $0.04 | **needs tweak** ruff's MySqlOperator -> SQLExecuteQueryOperator swap (ruff marks it safe) still keeps mysql_conn_id=, but the file is now labeled unverified with its error |
| [made-ml-in-prod-2021/airflow-examples](https://github.com/made-ml-in-prod-2021/airflow-examples/tree/96aaf7b) | 11 | 0/11 → 10/11 | 7 + 2 by Claude | 10/10 DAGs identical (13 fields) | 0 / 1 | 5: needs Claude (budget) ×2; ruff AIR311: unsafe fix withheld (unverified file) ×1; ruff AIR302: unsafe fix withheld (unverified file) ×1; ruff AIR301: no automatic fix ×1 | 19m52s | $0.08 | **clean** 10/11 import, all identical; Claude handled days_ago(0, 2) (positional hour) correctly |

Pins: cron/delta schedules pinned to Airflow 2's timetable, deterministic + by Claude. Render check: DAGs that import after the migration, every template field compared. Time is wall-clock for the whole repo (venvs, Airflow 2 baseline, agent, full render); Claude is that repo's spend.

## This round (2026-10-23): the top gaps fixed

Five things changed since the previous scorecard:

1. **`days_ago(N)`** now becomes `pendulum.today("UTC").add(days=-N)`. That is the same moment (UTC midnight today minus N days), still computed at parse time; a fixed date would change scheduling and catchup. It was checked against Airflow 2's own `days_ago` on its own version, and DAGs that use it are render-checked as a whole.
2. **The cron pin reads `start_date` the way Airflow 2's `DAG.__init__` does:** from the DAG, else from `default_args`, else from module-level values. It also understands `days_ago` and `pendulum.today("Z")`.
3. **Cron and timedelta DAGs left unpinned** are render-checked as a whole and listed under "⚠️ Run dates will shift".
4. **Files that fail on Airflow 3 before and after** are rebuilt without ruff's unsafe fixes or Claude's patches. Their remaining changes are marked unverified, and the file is listed with its error.
5. **Three more bugs the rerun found, fixed in separate commits:**
   - a Python set printed in a per-process order;
   - an unset trigger date: `None` on Airflow 2 versus `NOTSET` on Airflow 3, which mean the same thing;
   - the unverified rebuild re-applying changes the render check had reverted (`624c918`).

**Only 55 of the 146 DAGs** had at least one template field to compare. The other 91 have no templated fields, so "renders identically" is vacuous for them. For those, the evidence is the DagBag import plus the pin and `days_ago` proofs.

**dag.test():** 66 DAGs both run locally on Airflow 2 and import on Airflow 3 after the migration. 58 of them also pass `dag.test()` on Airflow 3. Of the other 8:
- 4 are BasPH's rocket-launch DAGs, which call a live external API (`KeyError: 'results'`). Not ours.
- 2 read a removed context key the agent listed as manual: `next_execution_date`, and a callable parameter named `execution_date`. Claude's budget ran out before them.
- 2 call `DAG.following_schedule`, which Airflow 3 removed. Nothing flagged it at the time of the run. The agent now does (`a8ff74b`), and those DAGs are excluded from the headline.

`dag.test()` failing on connections or credentials on both versions isn't counted against us.

## What's left (ranked by DAGs)

| # | Gap | DAGs | Note |
|---|---|---|---|
| 1 | Removed provider classes (`PostgresOperator`, `SnowflakeOperator`, `SubDagOperator`, ...) | ~7 scored (many more among excluded files) | Manual by nature; these files now show up as unverified. |
| 2 | ruff marks `MySqlOperator` → `SQLExecuteQueryOperator` **safe**, but it keeps `mysql_conn_id=` | AMPATH | The unverified label keeps it from being presented as done. A kwargs check on class swaps would catch it outright. |
| 3 | `prev_ds` / `next_ds` with no exact equivalent | BasPH 2 fields | Claude's patch was rejected; stays manual. |
| 4 | `days_ago(n, h)` with a positional `hour` | made-ml 1 | Goes to judgment; Claude got it right. A small rule extension. |
| 5 | Computed schedules (`schedule=export_schedule_interval`) | blockchain-etl 3 | Listed under "Run dates will shift". |
| 6 | `DAG.following_schedule()` (removed in Airflow 3) | BasPH 2 | Now flagged as manual (`a8ff74b`), with the 50 other DAG methods Airflow 3 removed. Their DAGs are excluded from the headline at every stage. |

## Runs and spend

- **Earlier scorecard:** $4.10 (passes 1–3, see git history).
- **This round, deterministic only ($0):**
  - pass 4: superseded by the fixes it found;
  - pass 5: all 10 repos.
- **This round, with Claude ($0.60 of the $0.90 cap):** pass 6 on the six repos with judgment sites in verified files. That's 15 calls at about $0.04 each.
- **Total:** $4.70 of the $5.00 limit.
- **Time:** BasPH's Claude pass took 97 minutes, because every patch re-ran the full 87-DAG check. The next commit makes that check re-test only the patched file.

## Harness limits

- Provider versions come from the repo's era (its Airflow pin, else its image tag, else its last-commit date).
- 60 DAG files that don't load on Airflow 2.11.2 are excluded from the score and listed below.
- Repo code ran locally with a throwaway HOME and no credentials in its environment.

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

## Excluded from the headline: DAG files calling a DAG method Airflow 3 removed

- `BasPH/data-pipelines-with-apache-airflow` `chapter09/tests/conftest.py`: `dag.clear`
- `BasPH/data-pipelines-with-apache-airflow` `chapter09/tests/airflowbook/operators/test_movielens_operator.py`: `dag.clear`
- `BasPH/data-pipelines-with-apache-airflow` `chapter05/dags/06_condition_dag.py`: `context["dag"].following_schedule`
- `BasPH/data-pipelines-with-apache-airflow` `chapter05/dags/05_condition_task.py`: `context["dag"].following_schedule`

## Unverified files (fail on Airflow 3 before and after; no unsafe fixes applied)

- `eliasbenaddou/personal_finance_de_project` `dags/monzo_dq_checks.py`: ModuleNotFoundError: No module named 'great_expectations.checkpoint.types'
- `mozilla/telemetry-airflow` `dags/adm_dma_export.py`: airflow.sdk.exceptions.AirflowNotFoundException: The conn_id `adm_sftp` isn't defined
- `mozilla/telemetry-airflow` `dags/adm_export.py`: airflow.sdk.exceptions.AirflowNotFoundException: The conn_id `adm_sftp` isn't defined
- `mozilla/telemetry-airflow` `dags/bhr_collection.py`: ModuleNotFoundError: No module named 'airflow.operators.subdag'
- `mozilla/telemetry-airflow` `dags/copy_deduplicate.py`: **kwargs: {'execution_date': '{{ execution_date.replace(hour=2, minute=0).isoformat() }}'}
- `mozilla/telemetry-airflow` `dags/fivetran_netsuite.py`: **kwargs: {'task_concurrency': 1}
- `mozilla/telemetry-airflow` `dags/looker.py`: KeyError: 'Variable lookml_generator_release_str does not exist.'
- `mozilla/telemetry-airflow` `dags/merino_jobs.py`: airflow.sdk.exceptions.AirflowNotFoundException: The conn_id `merino_elasticsearch_prod` isn't defined
- `mozilla/telemetry-airflow` `dags/probe_scraper.py`: ModuleNotFoundError: No module named 'airflow.utils.weekday'
- `BasPH/data-pipelines-with-apache-airflow` `chapter04/dags/listing_4_20.py`: ModuleNotFoundError: No module named 'airflow.providers.postgres.operators'
- `BasPH/data-pipelines-with-apache-airflow` `chapter07/digit_classifier/dags/chapter9_digit_classifier.py`: ModuleNotFoundError: No module named 'airflow.providers.amazon.aws.operators.s3_copy_object'
- `BasPH/data-pipelines-with-apache-airflow` `chapter07/insideairbnb/dags/chapter7_insideairbnb.py`: ValueError: Invalid `auto_remove` value True, expected one of 'never', 'success', or 'force'.
- `BasPH/data-pipelines-with-apache-airflow` `chapter08/dags/01_python.py`: KeyError: 'MOVIELENS_USER'
- `BasPH/data-pipelines-with-apache-airflow` `chapter09/dags/bash_operator_no_command.py`: TypeError: missing keyword argument 'bash_command'
- `BasPH/data-pipelines-with-apache-airflow` `chapter09/dags/dag_cycle.py`: AirflowDagCycleException: Cycle detected in Dag: chapter8_dag_cycle. Faulty task: t3
- `BasPH/data-pipelines-with-apache-airflow` `chapter09/dags/duplicate_task_ids.py`: airflow.sdk.exceptions.DuplicateTaskIdFound: Task id 'task' has already been added to the Dag
- `BasPH/data-pipelines-with-apache-airflow` `chapter09/dags/testme.py`: airflow.sdk.exceptions.DuplicateTaskIdFound: Task id 'test2' has already been added to the Dag
- `BasPH/data-pipelines-with-apache-airflow` `chapter10/docker/dags/01_docker.py`: KeyError: 'MOVIELENS_USER'
- `BasPH/data-pipelines-with-apache-airflow` `chapter10/kubernetes/dags/02_kubernetes.py`: ModuleNotFoundError: No module named 'airflow.providers.cncf.kubernetes.operators.kubernetes_pod'
- `BasPH/data-pipelines-with-apache-airflow` `chapter13/secretsbackend/dag.py`: ImportError: cannot import name 'SimpleHttpOperator' from 'airflow.providers.http.operators.http' (/Users/paddy/SDK/real-world-airflow/.venvs/BasPH__data-pipelines-with-apache-airflow-af3/lib/python3.
- `BasPH/data-pipelines-with-apache-airflow` `chapter14/dags/nyc_dag.py`: ModuleNotFoundError: No module named 'nyctransport'
- `BasPH/data-pipelines-with-apache-airflow` `chapter16/dags/01_aws_usecase.py`: ImportError: cannot import name 'AWSAthenaOperator' from 'airflow.providers.amazon.aws.operators.athena' (/Users/paddy/SDK/real-world-airflow/.venvs/BasPH__data-pipelines-with-apache-airflow-af3/lib/p
- `BasPH/data-pipelines-with-apache-airflow` `chapter17/dags/01_azure_usecase.py`: Reason: tried: '/opt/homebrew/opt/unixodbc/lib/libodbc.2.dylib' (no such file), '/System/Volumes/Preboot/Cryptexes/OS/opt/homebrew/opt/unixodbc/lib/libodbc.2.dylib' (no such file), '/opt/homebrew/opt/
- `BasPH/data-pipelines-with-apache-airflow` `chapter18/dags/gcp.py`: ImportError: cannot import name 'BigQueryExecuteQueryOperator' from 'airflow.providers.google.cloud.operators.bigquery' (/Users/paddy/SDK/real-world-airflow/.venvs/BasPH__data-pipelines-with-apache-ai
- `sanchitvj/sports_betting_analytics_engine` `dags/odds_batch_processing.py`: ImportError: cannot import name 'classproperty' from 'kafka.util' (/Users/paddy/SDK/real-world-airflow/.venvs/sanchitvj__sports_betting_analytics_engine-af3/lib/python3.12/site-packages/kafka/util.py)
- `sanchitvj/sports_betting_analytics_engine` `dags/odds_ingestion_dqc.py`: ImportError: cannot import name 'classproperty' from 'kafka.util' (/Users/paddy/SDK/real-world-airflow/.venvs/sanchitvj__sports_betting_analytics_engine-af3/lib/python3.12/site-packages/kafka/util.py)
- `sanchitvj/sports_betting_analytics_engine` `dags/snowflake_dbt_staging_marts.py`: ImportError: cannot import name 'SnowflakeOperator' from 'airflow.providers.snowflake.operators.snowflake' (/Users/paddy/SDK/real-world-airflow/.venvs/sanchitvj__sports_betting_analytics_engine-af3/li
- `sanchitvj/sports_betting_analytics_engine` `dags/sports_batch_backup.py`: ImportError: cannot import name 'classproperty' from 'kafka.util' (/Users/paddy/SDK/real-world-airflow/.venvs/sanchitvj__sports_betting_analytics_engine-af3/lib/python3.12/site-packages/kafka/util.py)
- `sanchitvj/sports_betting_analytics_engine` `dags/sports_batch_processing.py`: ImportError: cannot import name 'classproperty' from 'kafka.util' (/Users/paddy/SDK/real-world-airflow/.venvs/sanchitvj__sports_betting_analytics_engine-af3/lib/python3.12/site-packages/kafka/util.py)
- `sanchitvj/sports_betting_analytics_engine` `dags/sports_ingestion_backup.py`: ImportError: cannot import name 'classproperty' from 'kafka.util' (/Users/paddy/SDK/real-world-airflow/.venvs/sanchitvj__sports_betting_analytics_engine-af3/lib/python3.12/site-packages/kafka/util.py)
- `sanchitvj/sports_betting_analytics_engine` `dags/sports_ingestion_dqc.py`: ImportError: cannot import name 'classproperty' from 'kafka.util' (/Users/paddy/SDK/real-world-airflow/.venvs/sanchitvj__sports_betting_analytics_engine-af3/lib/python3.12/site-packages/kafka/util.py)
- `anilkulkarni87/airflow-docker` `dags/covidNyDaily.py`: ModuleNotFoundError: No module named 'airflow.providers.postgres.operators'
- `anilkulkarni87/airflow-docker` `dags/covidNyInitial.py`: ModuleNotFoundError: No module named 'airflow.providers.postgres.operators'
- `anilkulkarni87/airflow-docker` `dags/testBackfill.py`: ModuleNotFoundError: No module named 'airflow.providers.postgres.operators'
- `anilkulkarni87/airflow-docker` `dags/test_minio_local.py`: ModuleNotFoundError: No module named 'airflow.operators.python_operator'
- `anilkulkarni87/airflow-docker` `dags/test_postgres.py`: ModuleNotFoundError: No module named 'airflow.providers.postgres.operators'
- `astronomer/airflow-data-quality-demo` `dags/bigquery_examples/simple_bigquery.py`: ImportError: cannot import name 'BigQueryCreateEmptyTableOperator' from 'airflow.providers.google.cloud.operators.bigquery' (/Users/paddy/SDK/real-world-airflow/.venvs/astronomer__airflow-data-quality
- `astronomer/airflow-data-quality-demo` `dags/firebolt_examples/simple_firebolt.py`: ModuleNotFoundError: No module named 'airflow.operators.sql'
- `astronomer/airflow-data-quality-demo` `dags/great_expectations/great_expectations_bigquery.py`: ImportError: cannot import name 'BigQueryCreateEmptyTableOperator' from 'airflow.providers.google.cloud.operators.bigquery' (/Users/paddy/SDK/real-world-airflow/.venvs/astronomer__airflow-data-quality
- `astronomer/airflow-data-quality-demo` `dags/great_expectations/great_expectations_mlflow.py`: Reason: tried: '/opt/homebrew/opt/libomp/lib/libomp.dylib' (no such file), '/System/Volumes/Preboot/Cryptexes/OS/opt/homebrew/opt/libomp/lib/libomp.dylib' (no such file), '/opt/local/lib/libomp/libomp
- `astronomer/airflow-data-quality-demo` `dags/great_expectations/great_expectations_pandas_df.py`: ModuleNotFoundError: No module named 'great_expectations.checkpoint.types'
- `astronomer/airflow-data-quality-demo` `dags/great_expectations/great_expectations_redshift.py`: ModuleNotFoundError: No module named 'airflow.providers.amazon.aws.operators.redshift_sql'
- `astronomer/airflow-data-quality-demo` `dags/great_expectations/great_expectations_snowflake.py`: ImportError: cannot import name 'SnowflakeOperator' from 'airflow.providers.snowflake.operators.snowflake' (/Users/paddy/SDK/real-world-airflow/.venvs/astronomer__airflow-data-quality-demo-af3/lib/pyt
- `astronomer/airflow-data-quality-demo` `dags/great_expectations/great_expectations_snowflake_write_audit_publish.py`: ImportError: cannot import name 'SnowflakeOperator' from 'airflow.providers.snowflake.operators.snowflake' (/Users/paddy/SDK/real-world-airflow/.venvs/astronomer__airflow-data-quality-demo-af3/lib/pyt
- `astronomer/airflow-data-quality-demo` `dags/redshift_examples/simple_redshift_1.py`: ImportError: cannot import name 'datetime' from 'airflow.utils.dates' (/Users/paddy/SDK/real-world-airflow/.venvs/astronomer__airflow-data-quality-demo-af3/lib/python3.12/site-packages/airflow/utils/d
- `astronomer/airflow-data-quality-demo` `dags/redshift_examples/simple_redshift_2.py`: ModuleNotFoundError: No module named 'airflow.operators.sql'
- `astronomer/airflow-data-quality-demo` `dags/redshift_examples/simple_redshift_3.py`: ModuleNotFoundError: No module named 'airflow.operators.sql'
- `astronomer/airflow-data-quality-demo` `dags/snowflake_examples/complex_snowflake_transform.py`: ImportError: cannot import name 'SnowflakeOperator' from 'airflow.providers.snowflake.operators.snowflake' (/Users/paddy/SDK/real-world-airflow/.venvs/astronomer__airflow-data-quality-demo-af3/lib/pyt
- `astronomer/airflow-data-quality-demo` `dags/snowflake_examples/simple_snowflake.py`: ImportError: cannot import name 'SnowflakeOperator' from 'airflow.providers.snowflake.operators.snowflake' (/Users/paddy/SDK/real-world-airflow/.venvs/astronomer__airflow-data-quality-demo-af3/lib/pyt
- `astronomer/airflow-data-quality-demo` `dags/snowflake_examples/snowflake_dynamic_write_audit_publish.py`: ImportError: cannot import name 'SnowflakeOperator' from 'airflow.providers.snowflake.operators.snowflake' (/Users/paddy/SDK/real-world-airflow/.venvs/astronomer__airflow-data-quality-demo-af3/lib/pyt
- `astronomer/airflow-data-quality-demo` `dags/snowflake_examples/snowflake_write_audit_publish.py`: ImportError: cannot import name 'SnowflakeOperator' from 'airflow.providers.snowflake.operators.snowflake' (/Users/paddy/SDK/real-world-airflow/.venvs/astronomer__airflow-data-quality-demo-af3/lib/pyt
- `astronomer/airflow-data-quality-demo` `dags/snowflake_examples/taxi_snowflake.py`: ImportError: cannot import name 'SnowflakeOperator' from 'airflow.providers.snowflake.operators.snowflake' (/Users/paddy/SDK/real-world-airflow/.venvs/astronomer__airflow-data-quality-demo-af3/lib/pyt
- `astronomer/airflow-data-quality-demo` `dags/sql_examples/sql_check.py`: ModuleNotFoundError: No module named 'airflow.operators.sql'
- `astronomer/airflow-data-quality-demo` `dags/sql_examples/sql_check_redshift_etl.py`: ModuleNotFoundError: No module named 'airflow.operators.sql'
- `blockchain-etl/bitcoin-etl-airflow` `dags/bitcoinetl/build_export_dag.py`: ImportError: cannot import name 'bash_operator' from 'airflow.operators' (/Users/paddy/SDK/real-world-airflow/.venvs/blockchain-etl__bitcoin-etl-airflow-af3/lib/python3.12/site-packages/airflow/operat
- `blockchain-etl/bitcoin-etl-airflow` `dags/bitcoinetl/build_load_dag.py`: ModuleNotFoundError: No module named 'airflow.contrib'
- `blockchain-etl/bitcoin-etl-airflow` `dags/bitcoinetl/build_verify_streaming_dag.py`: ModuleNotFoundError: No module named 'airflow.contrib'
- `AMPATH/etl` `airflow/dags/all_etl_jobs_realtime.py`: ModuleNotFoundError: No module named 'pytz'
- `AMPATH/etl` `airflow/dags/amrs_garbage_collection.py`: ModuleNotFoundError: No module named 'airflow.providers.http'
- `AMPATH/etl` `airflow/dags/base_tables.py`: ModuleNotFoundError: No module named 'airflow.operators.bash_operator'
- `AMPATH/etl` `airflow/dags/branch_dag.py`: ModuleNotFoundError: No module named 'pytz'
- `AMPATH/etl` `airflow/dags/build_hiv_summaries.py`: ModuleNotFoundError: No module named 'pytz'
- `AMPATH/etl` `airflow/dags/eid_sync_job_daily.py`: ModuleNotFoundError: No module named 'pytz'
- `AMPATH/etl` `airflow/dags/eid_sync_job_weekly.py`: ModuleNotFoundError: No module named 'pytz'
- `AMPATH/etl` `airflow/dags/hiv_monthly_summary_nightly.py`: ModuleNotFoundError: No module named 'pytz'
- `AMPATH/etl` `airflow/dags/nightly_jobs.py`: ModuleNotFoundError: No module named 'pytz'
- `AMPATH/etl` `airflow/dags/surge_daily.py`: ModuleNotFoundError: No module named 'airflow.operators.bash_operator'
- `AMPATH/etl` `airflow/dags/sync_hiv_summary_only.py`: ModuleNotFoundError: No module named 'airflow.operators.bash_operator'
- `AMPATH/etl` `airflow/dags/test_new_etl_scripts.py`: ModuleNotFoundError: No module named 'pytz'
- `made-ml-in-prod-2021/airflow-examples` `dags/04_branching_1.py`: ModuleNotFoundError: No module named 'airflow.operators.python_operator'

## Render mismatches (full comparison)

- `BasPH/data-pipelines-with-apache-airflow` chapter7_insideairbnb.download_from_postgres._query: render mismatch on 10 of 10 runs, e.g. the run firing 2016-03-01 00:00 UTC (from the 23:00 sample): Airflow 2 rendered "SELECT * FROM listings WHERE download_date BETWEEN '2016-01-01' AND '2016-02-01'", the new template renders "RENDER ERROR Undef
- `BasPH/data-pipelines-with-apache-airflow` chapter7_insideairbnb.download_from_postgres._s3_key: render mismatch on 10 of 10 runs, e.g. the run firing 2016-03-01 00:00 UTC (from the 23:00 sample): Airflow 2 rendered 'listing-2016-02-01.csv', the new template renders "RENDER ERROR UndefinedError: 'prev_ds' is undefined"
