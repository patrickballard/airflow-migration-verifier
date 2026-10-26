import datetime

from airflow import DAG
from airflow.operators.bash_operator import BashOperator
from airflow.operators.python_operator import PythonOperator


def run(**context):
    print(context["execution_date"])


with DAG("d", schedule_interval="@daily", start_date=datetime.datetime(2024, 1, 1)) as dag:
    PythonOperator(task_id="t", python_callable=run)
    BashOperator(task_id="b", bash_command="echo {{ execution_date }}")
print(dag.dag_id, sorted(dag.task_dict))
