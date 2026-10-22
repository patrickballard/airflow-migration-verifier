"""Writes partitioned output. Its context use is indirect, so it needs a human (or Claude)."""
import datetime

from airflow import DAG
from airflow.operators.bash_operator import BashOperator
from airflow.operators.python_operator import PythonOperator


def _partition_path(ctx):
    # `ctx` is the task context, but only the caller knows that
    return ctx["execution_date"].strftime("%Y/%m/%d")


def write(**context):
    print("writing", _partition_path(context))


with DAG("partitions", schedule_interval="@hourly", start_date=datetime.datetime(2024, 1, 1), catchup=False) as dag:
    PythonOperator(task_id="write", python_callable=write) >> BashOperator(
        task_id="next_window", bash_command="echo next window starts {{ next_ds }}"
    )
