"""Which keyword arguments operator classes accept on the installed Airflow.

    python airflow_kwargs.py pkg.module.ClassName [...]   ->  {path: {"accepts": [...], "open": bool} | {"error": ...}}

Standalone (stdlib + airflow): the agent runs it with the target venv's python. A class's
accepted names are the named parameters of every __init__ up its MRO, as long as each one
passes **kwargs on; BaseOperator's **kwargs only exist to reject unknown arguments, so the
chain is closed there. "open": some __init__ really takes arbitrary keyword arguments.
"""
from __future__ import annotations

import importlib
import inspect
import json
import sys
import warnings

NAMED = (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)


def accepted(cls) -> dict:
    names: set[str] = set()
    for c in cls.__mro__:
        init = c.__dict__.get("__init__")
        if init is None:
            continue
        params = inspect.signature(init).parameters.values()
        names |= {p.name for p in params if p.kind in NAMED} - {"self"}
        if c.__name__ == "BaseOperator" or not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
            return {"accepts": sorted(names), "open": False}
    return {"accepts": sorted(names), "open": True}


def main(paths: list[str]) -> int:
    warnings.simplefilter("ignore")
    out = {}
    for path in paths:
        module, _, name = path.rpartition(".")
        try:
            out[path] = accepted(getattr(importlib.import_module(module), name))
        except Exception as e:  # noqa: BLE001 - reported per class
            out[path] = {"error": f"{type(e).__name__}: {e}"[:300]}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
