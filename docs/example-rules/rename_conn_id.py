"""Example custom rule (with an automatic fix): a connection your team renamed.

Copy this file to .airflow-verifier/rules/ in your repo and change OLD and NEW. docs/writing-rules.md walks
through it.
"""
import re
from pathlib import Path

from api_change_agent.rule_files import Site

ID = "rename-warehouse-conn-id"
DESCRIPTION = "The `legacy_warehouse` connection was renamed to `warehouse`."
DOCS = "https://airflow.apache.org/docs/apache-airflow/stable/howto/connection.html"
FIX = "deterministic"
NOTE = "same connection, new name"

OLD, NEW = "legacy_warehouse", "warehouse"
# a keyword argument whose name ends in conn_id (conn_id=, postgres_conn_id=, ...) set to the old name
PATTERN = re.compile(r"""(\b\w*conn_id\s*=\s*)(["'])""" + re.escape(OLD) + r"""\2""")


def find(path: Path, text: str) -> list[Site]:
    return [Site(text.count("\n", 0, m.start()) + 1, m.group(0), f"rename to {NEW!r}")
            for m in PATTERN.finditer(text)]


def apply(path: Path, text: str) -> str:
    return PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{NEW}{m.group(2)}", text)
