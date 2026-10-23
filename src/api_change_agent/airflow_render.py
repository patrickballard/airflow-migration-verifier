"""Render a repo's DAG templates for runs as the installed Airflow would schedule them.

    python airflow_render.py --repo DIR --files dags/a.py [...] --out OUT.json

Standalone (stdlib + airflow; the target functions need only the stdlib):
the agent runs it once with Airflow 2's python on the original code and once
with Airflow 3's python on the migrated code, then pairs the runs by the
wall-clock moment they fire (render_check.py).

For each DAG it builds sample moments in the DAG's timezone (23:00, 00:00,
month end, year end, leap day, both DST switches, ~20 ordinary ones), snaps
each to the next run the *scheduler-side* timetable fires, and renders every
task's template_fields for that run (template files such as .sh/.sql included).
"""
from __future__ import annotations

import argparse
import calendar
import copy
import datetime as dt
import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

ORDINARY = 20
HOUR = dt.timedelta(hours=1)


# --------------------------------------------------------------------------
# sample moments (pure stdlib)


def dst_transitions(tz_name: str, year: int) -> list[dt.datetime]:
    """UTC instants in `year` at which the zone's UTC offset changes."""
    tz = ZoneInfo(tz_name)
    t = dt.datetime(year, 1, 1, tzinfo=dt.timezone.utc)
    end = dt.datetime(year + 1, 1, 1, tzinfo=dt.timezone.utc)
    found, prev = [], t.astimezone(tz).utcoffset()
    while t < end:
        t += HOUR
        off = t.astimezone(tz).utcoffset()
        if off != prev:
            found.append(t)
            prev = off
    return found


def _year(start: dt.datetime) -> int:
    """The first leap year from the DAG's start (so the leap day can be sampled)."""
    for y in range(start.year, start.year + 4):
        if calendar.isleap(y):
            return y
    return start.year


def targets(tz_name: str, start: dt.datetime, end: dt.datetime | None, year: int | None = None):
    """[(label, aware UTC datetime)], sorted, within [start, end]."""
    tz = ZoneInfo(tz_name)

    def plain(d):  # stdlib datetime in `tz` (callers may pass pendulum objects)
        if d is None:
            return None
        return dt.datetime.fromtimestamp(d.timestamp(), tz) if d.tzinfo else d.replace(tzinfo=tz)

    start, end = plain(start), plain(end)
    y = year or _year(start)
    at = lambda *a: dt.datetime(*a, tzinfo=tz)  # noqa: E731 - wall-clock time in the DAG's zone
    items = [("23:00", at(y, 1, 15, 23)), ("00:00", at(y, 1, 16, 0)),
             ("month end 23:00", at(y, 1, 31, 23)), ("month start 00:00", at(y, 2, 1, 0)),
             ("year end 23:00", at(y, 12, 31, 23)), ("new year 00:00", at(y + 1, 1, 1, 0))]
    if calendar.isleap(y):
        items += [("leap eve 23:00", at(y, 2, 28, 23)), ("leap day 00:00", at(y, 2, 29, 0)),
                  ("leap day 23:00", at(y, 2, 29, 23)), ("after leap day 00:00", at(y, 3, 1, 0))]
    for t in dst_transitions(tz_name, y):
        d = t.date().isoformat()
        items += [(f"DST {d} -1h", t - HOUR), (f"DST {d} switch", t), (f"DST {d} +1h", t + HOUR)]
    lo = max(start, at(y, 1, 1, 0))
    hi = min(end, at(y, 12, 31, 23)) if end else at(y, 12, 31, 23)
    rng = random.Random(y)  # same moments in both Airflow versions
    span = max(int((hi - lo).total_seconds() // 3600), 1)
    for i in range(ORDINARY):
        items.append((f"ordinary {i + 1}", (lo + dt.timedelta(hours=rng.randrange(span))).replace(minute=0, second=0)))
    out = [(label, t.astimezone(dt.timezone.utc)) for label, t in items if t >= start and (end is None or t <= end)]
    return sorted(out, key=lambda x: x[1])


def dst_note(tz_name: str, year: int) -> str:
    switches = dst_transitions(tz_name, year)
    if not switches:
        return f"DST: n/a ({tz_name} has no DST)"
    return "DST: " + ", ".join(t.date().isoformat() for t in switches)


# --------------------------------------------------------------------------
# rendering (needs airflow)


def _norm(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, sort_keys=True)


def _has_xcom_arg(value, depth=0) -> bool:
    """An upstream task's output: it only has a value while the DAG runs, so it can't be render-compared."""
    if any(c.__name__.endswith("XComArg") for c in type(value).__mro__):
        return True
    if depth > 5:
        return False
    if isinstance(value, dict):
        return any(_has_xcom_arg(v, depth + 1) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_xcom_arg(v, depth + 1) for v in value)
    return False


def _scheduler_timetable(dag):
    try:  # Airflow 3: the SDK DAG's timetable is a definition; the scheduler deserializes its own
        from airflow.serialization.serialized_objects import DagSerialization
        return DagSerialization.deserialize_dag(DagSerialization.serialize_dag(dag)).timetable
    except ImportError:
        return dag.timetable


def _snap(tt, t):
    import pendulum
    from airflow.timetables.base import TimeRestriction

    t = pendulum.from_timestamp(t.timestamp(), tz="UTC")  # Airflow's timetables want pendulum datetimes

    restriction = TimeRestriction(earliest=t - dt.timedelta(days=1), latest=None, catchup=True)
    last = None
    for _ in range(100000):
        info = tt.next_dagrun_info(last_automated_data_interval=last, restriction=restriction)
        if info is None or info.run_after >= t:
            return info
        last = info.data_interval
    return None


def _render_af2(dag, task_id, info):
    from airflow.models.dagrun import DagRun
    from airflow.models.taskinstance import TaskInstance
    from airflow.utils.types import DagRunType

    logical = info.logical_date
    dr = DagRun(dag_id=dag.dag_id, run_id=f"scheduled__{logical.isoformat()}", execution_date=logical,
                data_interval=(info.data_interval.start, info.data_interval.end),
                run_type=DagRunType.SCHEDULED, external_trigger=False)
    dr.dag = dag
    task = copy.deepcopy(dag.get_task(task_id))
    ti = TaskInstance(task, run_id=dr.run_id)
    ti.dag_run = dr
    ti.render_templates(ti.get_template_context(ignore_param_exceptions=True))
    return task


def _render_af3(dag, task_id, info):
    from airflow.dag_processing.bundles.local import LocalDagBundle
    from airflow.sdk.api.datamodels._generated import DagRun, DagRunState, DagRunType, TIRunContext
    from airflow.sdk.execution_time.task_runner import RuntimeTaskInstance

    logical = getattr(info, "logical_date", None) or info.data_interval.start
    dr = DagRun(dag_id=dag.dag_id, run_id=f"scheduled__{logical.isoformat()}", logical_date=logical,
                data_interval_start=info.data_interval.start, data_interval_end=info.data_interval.end,
                run_after=info.run_after, start_date=info.run_after, end_date=None, run_type=DagRunType.SCHEDULED,
                state=DagRunState.RUNNING, conf={}, consumed_asset_events=[], partition_key=None)
    task = copy.deepcopy(dag.get_task(task_id))
    ti = RuntimeTaskInstance.model_construct(
        id=uuid.uuid4(), task_id=task_id, dag_id=dag.dag_id, run_id=dr.run_id, try_number=1,
        dag_version_id=uuid.uuid4(), map_index=-1, task=task, max_tries=0, start_date=info.run_after,
        bundle_instance=LocalDagBundle(name="dags-folder", path=str(Path(dag.fileloc).parent)))
    ti._ti_context_from_server = TIRunContext(dag_run=dr, max_tries=0)
    ti.render_templates(ti.get_template_context())
    return task


def render_file(repo: Path, rel: str) -> dict:
    import warnings

    warnings.simplefilter("ignore")
    from airflow.models.dagbag import DagBag

    af3 = importlib.util.find_spec("airflow.sdk") is not None
    kwargs = {"dag_folder": str(repo / rel)}
    if not af3:
        kwargs["include_examples"] = False
    bag = DagBag(**kwargs)
    out = {}
    for dag_id, dag in sorted(bag.dags.items()):
        tt = _scheduler_timetable(dag)
        tz = getattr(dag.timezone, "name", None) or str(dag.timezone)
        start = dag.start_date or dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
        runs, infos = [], []
        for label, t in targets(tz, start, dag.end_date):
            info = _snap(tt, t)
            if info is None or any(r["fires"] == info.run_after.isoformat() for r in runs):
                continue
            logical = getattr(info, "logical_date", None) or info.data_interval.start
            runs.append({"label": label, "target": t.isoformat(), "fires": info.run_after.isoformat(),
                         "logical": logical.isoformat() if logical else None,
                         "interval": [info.data_interval.start.isoformat(), info.data_interval.end.isoformat()]})
            infos.append(info)
        tasks = {}
        for task in dag.tasks:
            fields = {}
            for f in task.template_fields:
                fields[f] = {"raw": _norm(getattr(task, f, None)), "rendered": []}
                if _has_xcom_arg(getattr(task, f, None)):
                    fields[f]["unrenderable"] = "XComArg"
            for info in infos:
                try:
                    rendered = (_render_af3 if af3 else _render_af2)(dag, task.task_id, info)
                    for f in task.template_fields:
                        fields[f]["rendered"].append(_norm(getattr(rendered, f, None)))
                except Exception as e:  # noqa: BLE001 - a render error is a result, not a crash
                    msg = f"RENDER ERROR {type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else ''}"
                    for f in task.template_fields:
                        fields[f]["rendered"].append(msg)
            tasks[task.task_id] = {"fields": fields}
        out[f"{rel}::{dag_id}"] = {"file": rel, "dag_id": dag_id, "timetable": type(tt).__name__, "timezone": tz,
                                   "dst": dst_note(tz, _year(start)), "runs": runs, "tasks": tasks}
    return out, {Path(k).name: v.strip().splitlines()[-1] for k, v in bag.import_errors.items()}


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--files", nargs="+", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    if not os.environ.get("ACA_RENDER_CHILD"):
        # Fresh AIRFLOW_HOME (+ SQLite DB on Airflow 2, whose template context reads it), then re-exec.
        home = Path(tempfile.mkdtemp(prefix="aca-render-"))
        env = dict(os.environ, ACA_RENDER_CHILD="1", AIRFLOW_HOME=str(home), AIRFLOW__CORE__LOAD_EXAMPLES="False",
                   AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=f"sqlite:///{home / 'airflow.db'}", PYTHONWARNINGS="ignore")
        if importlib.util.find_spec("airflow.sdk") is None:
            db = subprocess.run([sys.executable, "-m", "airflow", "db", "migrate"], env=env, capture_output=True, text=True)
            if db.returncode:
                print(db.stderr[-2000:], file=sys.stderr)
                return 3
        return subprocess.run([sys.executable, __file__, *(argv or sys.argv[1:])], env=env).returncode
    import airflow

    result = {"version": airflow.__version__, "dags": {}, "errors": {}}
    repo = Path(a.repo).resolve()
    for rel in a.files:
        try:
            dags, errors = render_file(repo, rel)
            result["dags"].update(dags)
            result["errors"].update({rel: e for e in errors.values()})
        except Exception as e:  # noqa: BLE001
            result["errors"][rel] = f"{type(e).__name__}: {e}"
    Path(a.out).write_text(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
