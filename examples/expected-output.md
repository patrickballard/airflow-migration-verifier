## Airflow Migration Verifier: verify an Airflow 2 → 3 change (apache-airflow 3.3.2)

❌ **9 Airflow 3 problem(s) found in 1 file(s).** Each one is listed below with its fix.

Checked `examples/` as it is: what breaks if it runs on apache-airflow 3.3.2 unchanged.

### ⚠️ Run dates will shift: 1 schedule(s) not pinned

Airflow 3 runs cron schedules on `CronTriggerTimetable` and timedelta schedules on `DeltaTriggerTimetable`, where a run's logical date is the moment it fires; Airflow 2 used the interval timetables, where it is the start of the interval that just ended. For these DAGs every date derived from the run (`{{ ds }}`, `logical_date`, partition paths) moves by one interval unless you pin them as in Timetable pin, or restore the old defaults for all DAGs with `[scheduler] create_cron_data_intervals = True` and `create_delta_data_intervals = True`.

- `broken_dag.py:24` <code>"@daily"</code>: a cron schedule left on Airflow 3's default timetable; pin it, or set [scheduler] create_cron_data_intervals = True

### ⚠️ Unverified files

These files fail to import on Airflow 3 both before and after this change, so nothing that needs the import could be checked in them (render check, removed methods). Fix the import error below. Migrate mode fixes common causes, such as moved imports and `days_ago`.

- `broken_dag.py`: <code>ModuleNotFoundError: No module named 'airflow.operators.bash_operator'</code>

### DAG checks on apache-airflow 3.3.2

Each DAG file is imported (DagBag) on apache-airflow 3.3.2, before and after the change; DAGs aren't run. *Before* is the original Airflow 2 code, so it's expected to fail there.

| Check | Before | After |
|---|---|---|
| `broken_dag.py::import` | ❌ ModuleNotFoundError: No module named 'airflow.operators.bash_operator' | ❌ ModuleNotFoundError: No module named 'airflow.operators.bash_operator' |

### Airflow 2 code that breaks on Airflow 3
- `broken_dag.py:17` (`airflow3-context-keys`): `context["execution_date"]` uses a task-context key Airflow 3 removed; with its documented equivalent, write the line as `run_date = context["logical_date"].strftime("%Y-%m-%d")`
- `broken_dag.py:23` (`ruff AIR301`): `airflow.utils.dates.days_ago` is removed in Airflow 3.0. Migrate mode fixes it.
- `broken_dag.py:24` (`ruff AIR301`): `schedule_interval` is removed in Airflow 3.0. Migrate mode fixes it (ruff's fix).
- `broken_dag.py:28` (`ruff AIR302`): `airflow.operators.bash_operator.BashOperator` is moved into `standard` provider in Airflow 3.0. Migrate mode fixes it (ruff's fix).
- `broken_dag.py:31` (`airflow3-templates`): `yesterday_ds` uses a template variable Airflow 3 removed; with its documented equivalent, write the line as `bash_command="python extract.py --date {{ ds }} --previous {{ macros.ds_add(ds, -1) }}",`
- `broken_dag.py:33` (`ruff AIR302`): `airflow.operators.python_operator.PythonOperator` is moved into `standard` provider in Airflow 3.0. Migrate mode fixes it (ruff's fix).
- `broken_dag.py:36` (`ruff AIR301`): `provide_context` is removed in Airflow 3.0. Migrate mode fixes it.

<sub>Airflow Migration Verifier, verify mode: nothing in either tree was changed.</sub>
