"""Control: already portable, should work on both versions before and after."""
import datetime

from airflow import DAG
from airflow.operators.empty import EmptyOperator

with DAG("modern", schedule=None, start_date=datetime.datetime(2024, 1, 1), catchup=False) as dag:
    EmptyOperator(task_id="noop")
