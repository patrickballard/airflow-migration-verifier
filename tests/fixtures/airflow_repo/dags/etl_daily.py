"""Daily ETL, written for Airflow 2."""
import datetime

from airflow import DAG
from airflow.operators.bash_operator import BashOperator
from airflow.operators.python_operator import PythonOperator


def extract(**context):
    run_date = context["execution_date"]  # the run's logical date
    print(f"extracting for {run_date:%Y-%m-%d}")
    return run_date.strftime("%Y-%m-%d")


def load(**kwargs):
    day = kwargs.get("execution_date")
    print("loading", day, "after", kwargs["yesterday_ds"])


def summarize(templates_dict, **_):
    print(templates_dict["query"])


with DAG(
    dag_id="etl_daily",
    schedule_interval="@daily",
    start_date=datetime.datetime(2024, 1, 1),
    catchup=False,
) as dag:
    extract_task = PythonOperator(task_id="extract", python_callable=extract)
    load_task = PythonOperator(task_id="load", python_callable=load)
    report = BashOperator(
        task_id="report",
        bash_command="echo report for {{ execution_date }} after {{ yesterday_ds }}",
    )
    script = BashOperator(task_id="script", bash_command="scripts/report.sh")
    summary = PythonOperator(
        task_id="summary",
        python_callable=summarize,
        templates_dict={"query": "sql/summary.sql"},
        templates_exts=[".sql"],
    )
    extract_task >> load_task >> [report, script, summary]
