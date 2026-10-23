"""Do the operator classes ruff swapped in accept the call's keyword arguments on Airflow 3?

ruff's AIR fixes can replace one operator with another (MySqlOperator -> SQLExecuteQueryOperator)
and keep every argument, including ones the new class doesn't take (mysql_conn_id=). Airflow 3
rejects those when the DAG is parsed. Signatures come from the target venv (airflow_kwargs.py).
"""
from __future__ import annotations

import difflib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

SCRIPT = Path(__file__).with_name("airflow_kwargs.py")
_EMPTY = cst.Module(body=[])


@dataclass(frozen=True)
class Problem:
    line: int
    cls: str
    kwarg: str
    replaced: str | None
    reason: str
    call_line: int = 0


def _airflow_imports(text: str) -> dict[str, str]:
    """Local name -> dotted path, for names imported from airflow.*."""
    out = {}
    for st in cst.parse_module(text).body:
        for small in getattr(st, "body", ()) if isinstance(st, cst.SimpleStatementLine) else ():
            if isinstance(small, cst.ImportFrom) and small.module is not None and not isinstance(small.names, cst.ImportStar):
                module = _EMPTY.code_for_node(small.module)
                if module.startswith("airflow."):
                    for a in small.names:
                        name = _EMPTY.code_for_node(a.name)
                        out[a.asname.name.value if a.asname else name] = f"{module}.{name}"
    return out


def introduced_classes(original: str, final: str) -> dict[str, tuple[str, str | None]]:
    """Classes imported from airflow.* in `final` that `original` didn't import (from there), with the
    name each replaced at its call sites, when the change is a swap."""
    before, after = _airflow_imports(original), _airflow_imports(final)
    new = {n: p for n, p in after.items() if before.get(n) != p and n[:1].isupper()}
    replaced: dict[str, str | None] = {n: None for n in new}
    a, b = original.splitlines(), final.splitlines()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "replace":
            continue
        for old_line, new_line in zip(a[i1:i2], b[j1:j2]):
            for n in new:
                if re.search(rf"\b{n}\s*\(", new_line) and (m := re.search(r"\b([A-Z]\w*)\s*\(", old_line)) \
                        and m.group(1) != n:
                    replaced[n] = replaced[n] or m.group(1)
    return {n: (p, replaced[n]) for n, p in new.items()}


class _Calls(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, names):
        self.names, self.found = names, []

    def visit_Call(self, node: cst.Call):
        if isinstance(node.func, cst.Name) and node.func.value in self.names:
            for a in node.args:
                if a.keyword is not None:
                    self.found.append((self.get_metadata(PositionProvider, a).start.line, node.func.value,
                                       a.keyword.value, self.get_metadata(PositionProvider, node).start.line))


def calls(text: str, names) -> list[tuple[int, str, str, int]]:
    """(kwarg line, class, kwarg, call line) for keyword arguments passed to these classes."""
    v = _Calls(set(names))
    MetadataWrapper(cst.parse_module(text)).visit(v)
    return v.found


def problems(text: str, introduced: dict, signatures: dict) -> list[Problem]:
    out = []
    for line, name, kw, call_line in calls(text, introduced):
        path, old = introduced[name]
        sig = signatures.get(path) or {}
        if "accepts" not in sig or sig.get("open") or kw in sig["accepts"]:
            continue
        hint = (" (it takes `conn_id=`)" if kw.endswith("_conn_id") and "conn_id" in sig["accepts"] else "")
        swapped = f", swapped in by ruff for `{old}`," if old else ""
        out.append(Problem(line, name, kw, old, (
            f"`{name}`{swapped} doesn't accept `{kw}=` on Airflow 3{hint}; Airflow 3 rejects unknown "
            "arguments when the DAG file is parsed"), call_line))
    return out


def signatures(python: Path, paths: list[str], timeout: int = 300) -> dict:
    proc = subprocess.run([str(python), "-I", str(SCRIPT), *paths], capture_output=True, text=True, timeout=timeout)
    lines = proc.stdout.strip().splitlines()
    return json.loads(lines[-1]) if proc.returncode == 0 and lines else {}
