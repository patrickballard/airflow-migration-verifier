"""Differential render check: old templates on Airflow 2 vs new ones on Airflow 3.

Each side comes from airflow_render.py run in that version's venv. Runs are
paired by the wall-clock moment they fire (each version schedules them its own
way), and every changed template field must render identically on every pair.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

SCRIPT = Path(__file__).with_name("airflow_render.py")
EMPTY = {"null", "", "[]", "{}"}
TRIGGER = {"CronTriggerTimetable", "DeltaTriggerTimetable"}  # Airflow 3's defaults for cron / timedelta
RENAMED = {"logical_date": "execution_date"}  # new field -> Airflow 2 name (TriggerDagRunOperator)
UNSET_SAME = {"logical_date": ("null", '"NOTSET"')}  # unset on Airflow 2 (None) and 3 (NOTSET): both mean "now"
NEEDS_RUNNER = ("SUPERVISOR_COMMS",)  # Airflow 3 resolves XCom/Variables/Connections through the task runner


def _comparable(old: str, new: str) -> bool:
    """A pair says something about the migration: neither side needs a live task runner, and not both errored."""
    if any(n in old or n in new for n in NEEDS_RUNNER):
        return False
    return not (old.startswith("RENDER ERROR") and new.startswith("RENDER ERROR"))


@dataclass(frozen=True)
class Mismatch:
    label: str
    fires: str
    old: str
    new: str


@dataclass
class FieldResult:
    file: str
    dag_id: str
    task_id: str
    field: str
    runs: int
    labels: list[str] = field(default_factory=list)
    mismatches: list[Mismatch] = field(default_factory=list)
    dst: str = ""
    timetable: str = ""  # the migrated DAG's, as Airflow 3's scheduler sees it
    unpaired: str = ""  # why nothing was compared: no run fires at the same moment on both versions
    static: bool = False  # no Jinja on either side: every run renders the raw value, so "identical" proves nothing

    @property
    def unpinned(self) -> bool:
        """On Airflow 3's new default timetable: its run dates differ from Airflow 2 by design."""
        return self.timetable in TRIGGER


def render(python: Path, repo: Path, files: list[str], timeout: int = 1800, env: dict | None = None) -> dict:
    """`env` adds Airflow settings the repo's deployment uses (e.g. AIRFLOW__CORE__DEFAULT_TIMEZONE)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "render.json"
        proc = subprocess.run([str(python), "-I", str(SCRIPT), "--repo", str(repo), "--files", *files, "--out", str(out)],
                              capture_output=True, text=True, timeout=timeout, env={**os.environ, **(env or {})})
        if proc.returncode or not out.exists():
            raise RuntimeError(f"render failed (exit {proc.returncode}): {proc.stderr.strip()[-800:]}")
        return json.loads(out.read_text())


def _strings(raw: str):
    try:
        value = json.loads(raw) if raw[:1] in "[{\"" else raw
    except ValueError:
        value = raw
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, str):
            yield v
        elif isinstance(v, dict):
            stack += list(v.values())
        elif isinstance(v, list):
            stack += v


def changed_fields(old: dict, new: dict, changed_files: set[str]) -> set[tuple[str, str, str, str]]:
    """Template fields whose source changed: the raw value, or a template file it points at."""
    found = set()
    for key, dag in new["dags"].items():
        old_tasks = old["dags"].get(key, {}).get("tasks", {})
        for task_id, task in dag["tasks"].items():
            for name, f in task["fields"].items():
                old_raw = old_tasks.get(task_id, {}).get("fields", {}).get(name, {}).get("raw")
                refs = [s for s in _strings(f["raw"]) if s and any(c == s or c.endswith("/" + s) for c in changed_files)]
                if old_raw != f["raw"] or refs:
                    found.add((dag["file"], dag["dag_id"], task_id, name))
    return found


def compare(old: dict, new: dict, changed: set, whole_dags: set[str] = frozenset()) -> list[FieldResult]:
    """Compare `changed` fields (and every field of `whole_dags`) run by run, paired by fire time."""
    results = []
    for key, ndag in new["dags"].items():
        odag = old["dags"].get(key)
        if odag is None:
            continue
        o_at = {r["fires"]: i for i, r in enumerate(odag["runs"])}
        n_at = {r["fires"]: i for i, r in enumerate(ndag["runs"])}
        pairs = [(r, o_at[r["fires"]], i) for i, r in enumerate(ndag["runs"]) if r["fires"] in o_at]
        # Both versions snap the same sample moments to their next run, so the same schedule gives the same
        # fire times. A run on only one side means the schedule itself changed (e.g. a pin in the wrong zone).
        lone = sorted([(r, "old") for r in odag["runs"] if r["fires"] not in n_at]
                      + [(r, "new") for r in ndag["runs"] if r["fires"] not in o_at], key=lambda x: x[0]["fires"])
        for task_id, task in ndag["tasks"].items():
            for name, nf in task["fields"].items():
                if key not in whole_dags and (ndag["file"], ndag["dag_id"], task_id, name) not in changed:
                    continue
                old_fields = odag["tasks"].get(task_id, {}).get("fields", {})
                of = old_fields.get(name) or old_fields.get(RENAMED.get(name, ""))
                if of is None:
                    continue  # a new task or field: nothing to compare against
                if of["raw"] in EMPTY and nf["raw"] in EMPTY:
                    continue  # nothing to render (an unset cwd, env, ...)
                if of.get("unrenderable") or nf.get("unrenderable"):
                    continue  # e.g. an XComArg: its value only exists while the DAG runs
                if not pairs:  # say so: an empty comparison must never read as "identical"
                    why = ("the DAG has no schedule, so there are no runs to sample"
                           if not odag["runs"] and not ndag["runs"] else
                           "no run fires at the same moment on both versions")
                    results.append(FieldResult(ndag["file"], ndag["dag_id"], task_id, name, 0, dst=ndag.get("dst", ""),
                                               timetable=ndag.get("timetable", ""), unpaired=why))
                    continue
                usable = [(r, oi, ni) for r, oi, ni in pairs if _comparable(of["rendered"][oi], nf["rendered"][ni])]
                if not usable and not lone:
                    continue
                res = FieldResult(ndag["file"], ndag["dag_id"], task_id, name, len(usable) + len(lone),
                                  [r["label"] for r, _, _ in usable] + [r["label"] for r, _ in lone],
                                  dst=ndag.get("dst", ""), timetable=ndag.get("timetable", ""),
                                  static=_static(of) and _static(nf))
                for run, side in lone:
                    value = (of if side == "old" else nf)["rendered"][(o_at if side == "old" else n_at)[run["fires"]]]
                    missing = f"(no run fires at this moment on Airflow {'3' if side == 'old' else '2'})"
                    res.mismatches.append(Mismatch(run["label"], run["fires"], value if side == "old" else missing,
                                                   value if side == "new" else missing))
                for run, oi, ni in usable:
                    o, n = of["rendered"][oi], nf["rendered"][ni]
                    if o != n and UNSET_SAME.get(name) != (o, n):
                        res.mismatches.append(Mismatch(run["label"], run["fires"], of["rendered"][oi], nf["rendered"][ni]))
                results.append(res)
    return results


def _static(f: dict) -> bool:
    return "{{" not in f["raw"] and "{%" not in f["raw"] and all(v == f["raw"] for v in f["rendered"])


def summary(res: FieldResult) -> str:
    if res.unpaired or not res.runs:
        return f"not render-checked: {res.unpaired or 'no run fires at the same moment on both versions'}"
    if res.mismatches:
        m = res.mismatches[0]
        return (f"render mismatch on {len(res.mismatches)} of {res.runs} runs, e.g. the run firing {_when(m.fires)} "
                f"(from the {m.label} sample): Airflow 2 rendered {m.old!r}, the new template renders {m.new!r}")
    covered = []
    labels = " ".join(res.labels)
    if "23:00" in labels:
        covered.append("23:00")
    if "month end" in labels or "year end" in labels:
        covered.append("month/year end")
    if "leap day" in labels:
        covered.append("leap day")
    dst = res.dst.split(" (")[0] if res.dst.startswith("DST: n/a") else res.dst
    covered.append(dst)
    text = f"render-checked: identical across {res.runs} runs (incl. {', '.join(c for c in covered if c)})"
    return text + ("; the field holds no Jinja, so nothing in it can shift" if res.static else "")


def _when(fires: str) -> str:
    return fires.replace("T", " ")[:16] + (" UTC" if fires.endswith("+00:00") else fires[19:])


def mismatch_lines(results: list[FieldResult], limit: int = 6) -> list[str]:
    lines = []
    for r in results:
        for m in r.mismatches[:limit]:
            lines.append(f"{r.task_id}.{r.field}, run firing {_when(m.fires)} (from the {m.label} sample): "
                         f"Airflow 2 rendered {m.old!r}; the new template renders {m.new!r}")
    return lines


def task_spans(source: str) -> dict[str, tuple[int, int]]:
    """task_id -> (first, last) source line of the operator call that defines it."""
    spans: dict[str, tuple[int, int]] = {}

    class V(cst.CSTVisitor):
        METADATA_DEPENDENCIES = (PositionProvider,)

        def visit_Call(self, node: cst.Call) -> None:
            for a in node.args:
                if a.keyword is not None and a.keyword.value == "task_id" and isinstance(a.value, cst.SimpleString):
                    pos = self.get_metadata(PositionProvider, node)
                    spans[a.value.evaluated_value] = (pos.start.line, pos.end.line)

    MetadataWrapper(cst.parse_module(source)).visit(V())
    return spans
