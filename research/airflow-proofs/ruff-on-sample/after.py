import datetime

from airflow.sdk import DAG
from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.operators.python import PythonOperator


def run(**context):
    print(context["execution_date"])


with DAG("d", schedule="@daily", start_date=datetime.datetime(2024, 1, 1)) as dag:
    PythonOperator(task_id="t", python_callable=run)
    BashOperator(task_id="b", bash_command="echo {{ execution_date }}")
print(dag.dag_id, sorted(dag.task_dict))
