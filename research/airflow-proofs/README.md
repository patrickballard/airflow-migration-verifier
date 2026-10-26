# Airflow 2 → 3: the evidence behind `rules/airflow.yaml`

Collected on 2026-10-22 with Airflow 2.11.2 and 3.3.2, each installed with its official constraints (`constraints/`).

- **`cases/`**: three proofs, each run on both versions. On 2.11.2 all three work; on 3.3.2:
  - `01_schedule_interval`: `DAG(schedule_interval=...)` raises `TypeError`;
  - `02_operator_import_paths`: `airflow.operators.bash_operator` raises `ModuleNotFoundError`;
  - `03_execution_date_context`: `execution_date` is no longer a task-context key, which fails only when the task runs.
- **`ruff-on-sample/`**: ruff's `AIR3` fixes on a small DAG file (`before.py` → `after.py`, `changes.diff`), then the result run on Airflow 3 (`run_new.txt`). The DAG loads, but `context["execution_date"]` and the `{{ execution_date }}` template are left, and the task fails when it runs. That gap is what this action's own rules cover.
- **`surface/`**: every Airflow 2 public module, `DAG` and `BaseOperator` member and parameter, tried on Airflow 3: 315 of 628 public modules no longer import, 95 `DAG`/`BaseOperator` members are gone and 31 parameters were removed.
