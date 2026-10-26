"""A typical Airflow 2 DAG. It runs on Airflow 2.11; on Airflow 3 it breaks in five ways.

Run the verifier on it (see expected-output.md for what it reports):

    python -m api_change_agent verify --before examples --after examples --install-command auto
"""
from datetime import timedelta

from airflow import DAG
from airflow.operators.bash_operator import BashOperator  # module removed in Airflow 3
from airflow.operators.python_operator import PythonOperator  # module removed in Airflow 3
from airflow.utils.dates import days_ago  # removed in Airflow 3


def load_partition(**context):
    # `execution_date` is no longer in the task context: this raises KeyError when the task runs
    run_date = context["execution_date"].strftime("%Y-%m-%d")
    print(f"loading partition {run_date}")


with DAG(
    dag_id="daily_sales",
    start_date=days_ago(2),
    schedule_interval="@daily",  # renamed to `schedule`; and Airflow 3 runs @daily on a new timetable
    catchup=False,
    default_args={"retries": 1, "retry_delay": timedelta(minutes=5)},
) as dag:
    extract = BashOperator(
        task_id="extract",
        # on Airflow 3 `ds` is the day the run fires, not the day it covers: one day later
        bash_command="python extract.py --date {{ ds }} --previous {{ yesterday_ds }}",
    )
    load = PythonOperator(
        task_id="load",
        python_callable=load_partition,
        provide_context=True,  # a no-op since Airflow 2.0; Airflow 3 rejects it
    )
    extract >> load
