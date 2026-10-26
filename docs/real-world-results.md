# Real-world results: verify mode on 15 public Airflow 2 repos

On 2026-10-26 I ran the verifier on 15 public GitHub repos whose DAGs are written for Airflow 2. Each one pins an Airflow 2 version or runs an Airflow 2 Docker image. Each was cloned read-only. Nothing was pushed, and no issues or PRs were opened.

The question is the one the [Install](../README.md#install) workflow answers on a repo that hasn't migrated yet: **what breaks if these DAGs run on Airflow 3.3.2 as they are?**

## How it was run

- Each repo is a clean `git archive` of its default branch's head at the commit linked below. Every one of those commits was still the upstream head on 2026-10-26.
- Verify mode ran with the same tree as `before` and `after`. It used two venvs per repo, Airflow 2.11.2 and 3.3.2 on Python 3.12, built with Airflow's official constraints plus the providers each repo declares or imports.
- Script: [`research/verify-scan/scan.sh`](../research/verify-scan/scan.sh). Table: [`tabulate.py`](../research/verify-scan/tabulate.py). Per-repo numbers: [`results.json`](../research/verify-scan/results.json).

Each column means:
- **Fail on Airflow 3:** DAG files that import on Airflow 2 (or fail there for an unrelated reason, see below) and fail to import on 3.3.2.
- **Removed APIs in use:** code sites, not files. Each one is a removed or moved import, argument or DAG method, or a removed context key or template variable.
- **Run dates shift:** cron and timedelta schedules left on Airflow 3's new default timetables.
- **Not counted:** DAG files that fail on Airflow 2 as well, in the same way. See [False positives](#false-positives-found-and-fixed).

## Results

**288 of the 374 DAG files (77%) don't import on Airflow 3.3.2 as written.** Across all 15 repos, 1,329 code sites use something Airflow 3 removed, and 311 schedules would silently shift their run dates.

| Repo | Airflow 2 | DAG files | Fail on Airflow 3 | Removed APIs in use | Run dates shift | Not counted (fail on Airflow 2 too) |
|---|---|---|---|---|---|---|
| [BasPH/data-pipelines-with-apache-airflow](https://github.com/BasPH/data-pipelines-with-apache-airflow/tree/d5b18a7) | apache-airflow~=2.0.0 | 87 | 78 | 451 | 81 | 9 |
| [DaliGuerfali/big-data](https://github.com/DaliGuerfali/big-data/tree/df84c1f) | apache-airflow==2.8.1 | 3 | 3 | 4 | 3 | 0 |
| [anilkulkarni87/airflow-docker](https://github.com/anilkulkarni87/airflow-docker/tree/8b87bef) | apache/airflow:2.7.3 image | 6 | 6 | 8 | 0 | 0 |
| [eliasbenaddou/personal_finance_de_project](https://github.com/eliasbenaddou/personal_finance_de_project/tree/e34718b) | apache/airflow:2.2.4 image | 5 | 4 | 7 | 3 | 1 |
| [made-ml-in-prod-2021/airflow-examples](https://github.com/made-ml-in-prod-2021/airflow-examples/tree/96aaf7b) | apache/airflow:2.3.0 image | 10 | 10 | 44 | 9 | 0 |
| [mozilla/telemetry-airflow](https://github.com/mozilla/telemetry-airflow/tree/cf609dd) | apache-airflow==2.10.5 | 58 | 50 | 73 | 48 | 1 |
| [sanchitvj/sports_betting_analytics_engine](https://github.com/sanchitvj/sports_betting_analytics_engine/tree/855a744) | apache-airflow>=2.10.1 (no upper bound) | 8 | 1 | 27 | 3 | 7 |
| [4lcatrust/deso-query](https://github.com/4lcatrust/deso-query/tree/185f498) | apache-airflow==2.10.5 | 9 | 1 | 3 | 2 | 6 |
| [DEDSEC0708/orchestrated-warehouse](https://github.com/DEDSEC0708/orchestrated-warehouse/tree/ddc4a16) | apache-airflow==2.10.5 | 5 | 4 | 1 | 3 | 0 |
| [Chirag-Kathuria-009/DORA-Pipeline](https://github.com/Chirag-Kathuria-009/DORA-Pipeline/tree/a0e2adf) | apache-airflow==2.8.0 | 1 | 0 | 0 | 1 | 0 |
| [DhanushPillay/tx-recon](https://github.com/DhanushPillay/tx-recon/tree/85af5ff) | apache-airflow==2.11.2 | 1 | 0 | 0 | 1 | 0 |
| [Diego-Oliveira-SS/airflow](https://github.com/Diego-Oliveira-SS/airflow/tree/ca3a794) | apache/airflow:2.9.3 image | 4 | 4 | 7 | 4 | 0 |
| [leadwithnav/airflow-training](https://github.com/leadwithnav/airflow-training/tree/a1f7467) | apache/airflow:2.3.0 image | 103 | 65 | 314 | 83 | 31 |
| [Boaz-Airflow/boaz-airflow-study](https://github.com/Boaz-Airflow/boaz-airflow-study/tree/978804d) | apache/airflow:2.5.1 image | 53 | 45 | 339 | 52 | 6 |
| [rshukla0101/ApacheAirflow](https://github.com/rshukla0101/ApacheAirflow/tree/19c5bc5) | apache/airflow:2.5.1 image | 21 | 17 | 51 | 18 | 2 |
| **15 repos** | | **374** | **288** | **1329** | **311** | 63 |

## What came up most

| Kind | Sites |
|---|---|
| Removed import, argument or attribute (ruff `AIR301`) | 664 |
| Moved to a provider, old path gone (ruff `AIR302`) | 506 |
| Removed template variable (`{{ execution_date }}`, `{{ prev_ds }}`, ...) | 93 |
| Removed context key (`context["execution_date"]`, ...) | 55 |
| Removed DAG method (`following_schedule`, `clear`, ...) | 11 |

- **`schedule_interval=`** is the single biggest import failure: Airflow 3 rejects it with a `TypeError` when it parses the file. ruff's `AIR3` fixes rewrite it.
- **Old operator paths** (`airflow.operators.dummy`, `bash_operator`, `python_operator`, `subdag`, `airflow.contrib`) and `airflow.utils.dates.days_ago` account for most of the other import failures.
- **`airflow.datasets` is gone** (renamed to assets). It broke 4 of the 5 DAG files in [DEDSEC0708/orchestrated-warehouse](https://github.com/DEDSEC0708/orchestrated-warehouse), which has an open Dependabot PR bumping it to 3.3.2.
- **Silent changes** are the ones nothing else flags:
  - `{{ execution_date }}`, `{{ yesterday_ds }}`, `{{ prev_ds }}` and `{{ conf }}` in templates;
  - `context["execution_date"]` and `context.get("execution_date", "")` in Python (on Airflow 3 the `.get` quietly returns the default);
  - **311 schedules whose run dates shift**: `ds` and `logical_date` move by one interval, and `data_interval_start == data_interval_end`. A DAG that loads "the rows between the interval's start and end" loads nothing. Checked on 3.3.2 for both `CronTriggerTimetable` and `DeltaTriggerTimetable`.

Three of the repos (the *Data Pipelines with Apache Airflow* book code, leadwithnav/airflow-training and Boaz-Airflow/boaz-airflow-study) are course material. They account for most of the removed-API sites, and they include DAGs that are broken on purpose, such as cycles and duplicate task IDs.

## False positives found and fixed

These came up while reading the reports line by line, before any number above was counted. Each was fixed in the verifier, with a test, and every repo was re-scanned afterwards.

1. **Environment failures counted as Airflow 3 problems.** 15 DAG files in 5 repos failed to import on Airflow 3 because of something the scan's environment didn't have: a Variable read at parse time (`MINIO_ACCESS_KEY`), a connection (`merino_elasticsearch_prod`), a package (`clickhouse_connect`, `boto3`), or a library that doesn't run on Python 3.12 (`kafka-python`).
   - **Fix:** verify mode now also imports the `before` tree on Airflow 2. A file whose Airflow 3 failure is the Airflow 2 failure again, or doesn't involve Airflow at all, is listed under *Other notes* and not counted ([`3412f99`](https://github.com/patrickballard/airflow-migration-verifier/commit/3412f99), [`f2fe8cd`](https://github.com/patrickballard/airflow-migration-verifier/commit/f2fe8cd)).
   - **The first version of this fix over-corrected.** It stopped counting *any* file that also fails on Airflow 2, and that hid 31 real breaks: files that fail on Airflow 2 for a missing Variable, but on Airflow 3 fail earlier, on `schedule_interval` or a removed module. The final rule compares the two errors.
2. **Deprecated import paths reported as removed.** ruff's `AIR301`/`AIR302` call `airflow.operators.email.EmailOperator` and `airflow.operators.python.get_current_context` removed or moved. Both still import on 3.3.2, with a deprecation warning.
   - **Fix:** verify mode now tries each import path ruff names on the Airflow 3 venv, and lists the ones that still work as deprecated, not as problems ([`3412f99`](https://github.com/patrickballard/airflow-migration-verifier/commit/3412f99)). 3 sites.
3. **A render-check asymmetry.** The harness gave the Airflow 3 run a start date but left the Airflow 2 one `None`. So `--started {{ dag_run.start_date }}` in [Chirag-Kathuria-009/DORA-Pipeline](https://github.com/Chirag-Kathuria-009/DORA-Pipeline) was reported as rendering differently.
   - **Fix:** both versions now get the same start date ([`03e6d8d`](https://github.com/patrickballard/airflow-migration-verifier/commit/03e6d8d)). The remaining difference in that field, `{{ run_id }}`, is real: it changes with the timetable.
4. **Parameters named like removed context keys, on functions Airflow never calls with the context.** 5 sites in 3 repos:
   - in [DaliGuerfali/big-data](https://github.com/DaliGuerfali/big-data), `conf=None` on an operator's `__init__` and on two helpers;
   - in leadwithnav/airflow-training and rshukla0101/ApacheAirflow, an `execution_date` parameter on a function that's never used as a callable, or only in a comment.

   Airflow only passes context keys by parameter name to functions it calls with the context.
   - **Fix:** such parameters are now flagged only on a function passed as `python_callable`, an `@task`, or a non-method that takes `**kwargs` ([`f5f7c6e`](https://github.com/patrickballard/airflow-migration-verifier/commit/f5f7c6e)).

I also spot-checked 15 findings at random, at least one of each kind, against the source and against Airflow 3.3.2. All 15 were correct.

## What's still uncertain

- **Computed schedules.** "Run dates shift" includes schedules the verifier can't read statically, such as a `schedule` parameter passed through a DAG factory. Some of those factories are only ever called with `schedule=None`. The report labels each one as computed; read those as "check this", not "this breaks".
- **Course repos inflate the totals.** Of the 1,329 removed-API sites, 1,104 are in the three course repos. The other 12 repos have 225 sites, and 100 of their 131 DAG files don't import on Airflow 3.
- **Import-time only.** These runs import DAGs but don't run them (verify mode never runs a PR's tasks). Breaks inside task code are found only where they're visible in the source: removed context keys, removed DAG methods.
