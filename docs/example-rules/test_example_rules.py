"""Tests for the example rules: the shape a custom rule's test takes. Copy and adapt one per rule."""
from pathlib import Path

from api_change_agent.rule_files import load_rule_file

HERE = Path(__file__).parent
owner = load_rule_file(HERE / "require_dag_owner.py")
rename = load_rule_file(HERE / "rename_conn_id.py")


def test_owner_missing_or_default_is_found():
    text = (
        "from airflow import DAG\n"
        "from airflow.sdk import dag\n"
        "a = DAG('a', default_args={'retries': 1})\n"
        "b = DAG('b', default_args={'owner': 'airflow'})\n"
        "@dag(schedule=None)\n"
        "def c(): ...\n"
    )
    assert [s.line for s in owner.find(Path("dags/x.py"), text)] == [3, 4, 5]


def test_owner_set_or_unknowable_is_not_found():
    text = (
        "from airflow import DAG\n"
        "a = DAG('a', default_args={'owner': 'data-platform'})\n"
        "b = DAG('b', default_args=DEFAULTS)  # defined elsewhere: not guessed\n"
    )
    assert owner.find(Path("dags/x.py"), text) == []


def test_rename_finds_and_fixes_every_conn_id_keyword():
    text = (
        "t1 = SQLExecuteQueryOperator(task_id='a', conn_id='legacy_warehouse', sql='x')\n"
        't2 = PostgresHook(postgres_conn_id="legacy_warehouse")\n'
        "note = 'legacy_warehouse'  # not a conn_id argument: left alone\n"
    )
    assert [s.line for s in rename.find(Path("dags/x.py"), text)] == [1, 2]
    fixed = rename.apply(Path("dags/x.py"), text)
    assert "conn_id='warehouse'" in fixed and 'postgres_conn_id="warehouse"' in fixed
    assert "note = 'legacy_warehouse'" in fixed
    assert rename.find(Path("dags/x.py"), fixed) == []  # a fix leaves nothing to find
