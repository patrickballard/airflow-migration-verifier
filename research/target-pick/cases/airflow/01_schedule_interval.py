import datetime
from airflow import DAG
dag = DAG("d", schedule_interval="@daily", start_date=datetime.datetime(2024, 1, 1))
print("schedule_interval ok:", dag.dag_id)
