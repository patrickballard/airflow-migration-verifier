"""Which dotted names import on the installed Airflow.

    python airflow_imports.py airflow.operators.email.EmailOperator [...]   ->  {name: true | false}

Standalone (stdlib + airflow): verify mode runs it with the Airflow 3 venv's python, to tell a path Airflow 3
removed from one it only deprecated (it still imports, with a warning).
"""
from __future__ import annotations

import importlib
import json
import sys
import warnings


def imports(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:  # noqa: BLE001 - not a module: try it as an attribute of its parent
        pass
    module, _, attr = name.rpartition(".")
    try:
        getattr(importlib.import_module(module), attr)
        return True
    except Exception:  # noqa: BLE001 - removed, moved, or fails on import: all mean "doesn't import"
        return False


if __name__ == "__main__":
    warnings.simplefilter("ignore")
    print(json.dumps({name: imports(name) for name in sys.argv[1:]}))
