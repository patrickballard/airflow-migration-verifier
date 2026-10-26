"""Example custom rule (detection only): every DAG names a real owner.

Copy this file to .airflow-verifier/rules/ in your repo to use it. docs/writing-rules.md walks through it.
"""
import ast
from pathlib import Path

from api_change_agent.rule_files import Site

ID = "require-dag-owner"
DESCRIPTION = "Every DAG sets `owner` in its default_args to a team, not Airflow's default `airflow`."
DOCS = "https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html#default-arguments"
FIX = "manual"


def _owner(default_args: ast.AST | None) -> str | None:
    """The literal owner in a `default_args={...}` dict, or None."""
    if not isinstance(default_args, ast.Dict):
        return None
    for key, value in zip(default_args.keys, default_args.values):
        if isinstance(key, ast.Constant) and key.value == "owner" and isinstance(value, ast.Constant):
            return value.value
    return None


def find(path: Path, text: str) -> list[Site]:
    sites = []
    for node in ast.walk(ast.parse(text)):
        # DAG(...) and the @dag(...) decorator, under any import name ending in DAG / dag
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name not in ("DAG", "dag"):
            continue
        default_args = next((k.value for k in node.keywords if k.arg == "default_args"), None)
        if isinstance(default_args, ast.Name):
            continue  # a variable defined elsewhere: can't tell from here, so don't guess
        owner = _owner(default_args)
        if owner in (None, "airflow"):
            sites.append(Site(node.lineno, ast.get_source_segment(text, node.func) or name,
                              "set default_args={'owner': '<your team>'} so failures reach the right people"))
    return sites
