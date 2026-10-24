"""PR body rendering."""
from __future__ import annotations

import html
import textwrap
from dataclasses import dataclass

MAX_LINES = 6
MAX_WIDTH = 140


@dataclass(frozen=True)
class Row:
    path: str
    line: int
    before: str
    after: str
    rule_id: str
    changelog: str
    note: str = ""  # why this change, shown under the "after" value
    raw_before: str = ""  # exact source lines (not dedented), so one site can be reverted
    raw_after: str = ""


def _cell(code: str, empty: str = "(removed)") -> str:
    lines = textwrap.dedent(code).strip("\n").splitlines() or [""]
    truncated = len(lines) > MAX_LINES
    lines = [ln if len(ln) <= MAX_WIDTH else ln[: MAX_WIDTH - 1] + "…" for ln in lines[:MAX_LINES]]
    if truncated:
        lines.append("…")
    body = "<br>".join(html.escape(ln, quote=False).replace("|", "&#124;") for ln in lines)
    return f"<code>{body}</code>" if body else f"<i>{empty}</i>"


def render_table(rows: list[Row], blob_base: str | None) -> str:
    out = ["| Location | Before → After | Rule | Changelog |", "|---|---|---|---|"]
    for r in rows:
        loc = f"{r.path}:{r.line}"
        if blob_base:
            loc = f"[{loc}]({blob_base}/{r.path}#L{r.line})"
        change = f"{_cell(r.before, empty='(not declared)')} → {_cell(r.after)}"
        if r.note:
            change += f"<br><sub>{html.escape(r.note, quote=False).replace('|', '&#124;')}</sub>"
        link = f"[changelog]({r.changelog})" if r.changelog else "—"
        out.append(f"| {loc} | {change} | `{r.rule_id}` | {link} |")
    return "\n".join(out)


def logical_date_sites(rows: list[Row]) -> tuple[list[tuple[str, int, str]], list[tuple[str, int, str]]]:
    """Lines this PR changed to read `logical_date`: (from execution_date, from any other key).

    Paired line by line from each row's before/after, so it lists exactly what changed,
    whichever rule (or Claude) changed it.
    """
    renamed, other = [], []
    for r in rows:
        before, after = r.before.splitlines(), r.after.splitlines()
        pairs = zip(before, after) if len(before) == len(after) else [("\n".join(before), "\n".join(after))]
        for i, (old, new) in enumerate(pairs):
            if "logical_date" not in new or "logical_date" in old:
                continue
            target = renamed if "execution_date" in old else other
            target.append((r.path, r.line + i, new.strip()))
    return renamed, other


def _link(path, line, blob_base) -> str:
    loc = f"{path}:{line}"
    return f"[`{loc}`]({blob_base}/{path}#L{line})" if blob_base else f"`{loc}`"


def _manual_runs_section(rows: list[Row], blob_base: str | None) -> list[str]:
    renamed, other = logical_date_sites(rows)
    if not renamed and not other:
        return []

    def item(path, line, code):
        return f"- {_link(path, line, blob_base)} <code>{html.escape(code[:160], quote=False)}</code>"

    out = ["### ⚠️ Manual runs", "",
           "In Airflow 3 a manually triggered run can have **no logical date**, and then `logical_date` is `None`. "
           "Airflow 2 always set `execution_date` (for a manual run, to the time it was triggered). "
           "Scheduled runs are not affected.", ""]
    if renamed:
        out += [f"These {len(renamed)} line(s) now read `logical_date` where they read `execution_date`:", ""]
        out += [item(*s) for s in renamed] + [""]
    if other:
        out += [f"These {len(other)} line(s) now read `logical_date` in place of another removed key:", ""]
        out += [item(*s) for s in other] + [""]
    out += ["If your team triggers these DAGs manually without a logical date, fall back to the time the run "
            "was triggered (`run_after` is always set):", "",
            "```python", 'context["logical_date"] or context["dag_run"].run_after', "```", "",
            "Use your own name for the context (`kwargs`, `ctx`, ...). In a Jinja template: "
            "`{{ logical_date or dag_run.run_after }}`.", ""]
    return out


TIMETABLE_PIN = (
    "### Timetable pin\n\n"
    "Airflow 3 changed the default timetable for cron schedules: with `[scheduler] create_cron_data_intervals = False` "
    "(the new default) a cron DAG runs on `CronTriggerTimetable`, where a run's logical date is the moment it fires, "
    "whereas Airflow 2 used `CronDataIntervalTimetable`, where the logical date is the start of the interval that just "
    "ended. Left alone, every date derived from it (`{{ ds }}`, `{{ logical_date }}`, partition paths) would move by one "
    "interval for a run that fires at the same wall-clock time. This PR pins each cron DAG to "
    "`CronDataIntervalTimetable(<same cron>, timezone=<the DAG's timezone>)`, which is exactly the timetable Airflow 2 "
    "built for it, so runs fire at the same times with the same dates; the render check below compares them run by run. "
    "Teams can adopt the new semantics deliberately later by removing the pin and reviewing every date the DAG "
    "derives from its run. To keep the old behaviour for all DAGs instead of per DAG, set "
    "`[scheduler] create_cron_data_intervals = True` (env `AIRFLOW__SCHEDULER__CREATE_CRON_DATA_INTERVALS=True`) and "
    "drop the pins; either choice preserves Airflow 2's run dates.")


def _timezone_section(tz, naive_pins, blob_base) -> list[str]:
    """Which zone the pins of DAGs with a naive start_date use, and why; loud when it was assumed."""
    if not naive_pins or tz is None:
        return []
    if tz.tz:
        return [f"DAGs with a naive `start_date` are scheduled in `[core] default_timezone`. This repo sets "
                f"`default_timezone = {tz.tz}` ({tz.where()}), so their pins use `timezone=\"{tz.tz}\"`, and the "
                "render check ran with the same setting.", ""]
    sites = [f"- {_link(r.path, r.line, blob_base)}" for r in naive_pins]
    return ["### ⚠️ Timezone assumed: UTC", "",
            "These DAGs have a naive `start_date`, so Airflow schedules them in `[core] default_timezone`. No such "
            "setting was found in this repo (`airflow.cfg`, or `AIRFLOW__CORE__DEFAULT_TIMEZONE` in `.env` files, "
            "docker-compose, Helm values or Dockerfiles), so their pins assume **`UTC`**, Airflow's default. "
            "If your deployment sets another zone, change `timezone=` in each of these pins to it before merging, "
            "or the DAGs will fire at different times than they do on Airflow 2:", "", *sites, ""]


def _unpinned_section(unpinned, blob_base) -> list[str]:
    if not unpinned:
        return []
    out = [f"### ⚠️ Run dates will shift: {len(unpinned)} schedule(s) not pinned", "",
           "Airflow 3 runs cron schedules on `CronTriggerTimetable` and timedelta schedules on "
           "`DeltaTriggerTimetable`, where a run's logical date is the moment it fires; Airflow 2 used the interval "
           "timetables, where it is the start of the interval that just ended. For these DAGs every date derived from "
           "the run (`{{ ds }}`, `logical_date`, partition paths) moves by one interval unless you pin them as in "
           "Timetable pin, or restore the old defaults for all DAGs with `[scheduler] create_cron_data_intervals = "
           "True` and `create_delta_data_intervals = True`.", ""]
    for u in unpinned:
        why = u["reason"].split(": ", 1)[1] if ": " in u["reason"] else u["reason"]
        line = f"- {_link(u['path'], u['line'], blob_base)} <code>{html.escape(u['schedule'][:120], quote=False)}</code>: {why}"
        if u.get("evidence"):
            line += f". ❌ {html.escape(u['evidence'][:400], quote=False)}"
        out.append(line)
    return out + [""]


def _unverified_section(unverified, blob_base, verify=False) -> list[str]:
    if not unverified:
        return []
    why = ("These files fail to import on Airflow 3 both before and after this change, so nothing that needs the "
           "import could be checked in them (render check, removed methods). Fix the import error below."
           if verify else
           "These files fail to import on Airflow 3 both before and after this PR, so nothing in them could be "
           "checked (DagBag import, `dag.test()`, render check). Their changes above are marked unverified; ruff's "
           "unsafe fixes and Claude's patches were not applied to them. Fix the error and run the agent again.")
    out = ["### ⚠️ Unverified files", "", why, ""]
    out += [f"- `{u.path}`: <code>{html.escape(u.error[:300], quote=False)}</code>" for u in unverified]
    return out + [""]


def _render_section(results) -> list[str]:
    from .render_check import summary  # local: render_check imports nothing from here, but keep report light

    out = ["### Render check", "",
           "Old templates rendered on Airflow 2, new ones on Airflow 3, for runs as each version schedules them, "
           "paired by the moment they fire.", "", "| Template field | Result |", "|---|---|"]
    for r in sorted(results, key=lambda r: (r.file, r.dag_id, r.task_id, r.field)):
        text = html.escape(summary(r), quote=False).replace("|", "&#124;")
        out.append(f"| `{r.file}` `{r.dag_id}.{r.task_id}.{r.field}` | {'✅' if not r.mismatches else '❌'} {text} |")
    return out + [""]


def _dag_status(outcome, test_id) -> str:
    if outcome is None:
        return "—"
    if test_id not in outcome.ids:
        owner = test_id.split("::", 1)[0]
        return "❌ not reached: file failed to import" if f"{owner}::import" in outcome.failures else "—"
    if test_id in outcome.failures:
        why = outcome.messages.get(test_id, "")
        return "❌ " + html.escape(why[:140], quote=False).replace("|", "&#124;") if why else "❌"
    return "✅"


def _dag_table(before, after, target, verify=False) -> list[str]:
    ids = list(dict.fromkeys([*before.ids, *(after.ids if after else ())]))
    ran_dags = any("::import" not in i for i in ids)  # dag.test() rows exist only when it ran
    what = ("Each DAG file is imported (DagBag) and each DAG run with `dag.test()`" if ran_dags
            else "Each DAG file is imported (DagBag); DAGs aren't run")
    lines = [f"### DAG checks on {target}", "",
             f"{what}, before and after {'the change' if verify else 'this PR'}.", "",
             "| Check | Before | After |", "|---|---|---|"]
    for test_id in ids:
        lines.append(f"| `{test_id}` | {_dag_status(before, test_id)} | {_dag_status(after, test_id)} |")
    return lines + [""]


def render_body(*, package, rows, rules, pin_minimum, pin_conflicts, lock_notes, skipped_rules, skipped, manual,
                unparsable, tests_baseline, tests_after, new_failures, blob_base, rules_path,
                rule_notes=None, dep_notes=(), title="", tools=(), dag_target=None, llm_usage=None,
                llm_notes=(), render_results=(), timezone=None, naive_pins=(), unverified=(), unpinned=(),
                verify=False) -> str:
    rule_notes = rule_notes or {}
    applied = sorted({r.rule_id for r in rows})
    name = "Airflow Migration Verifier" if dag_target else "api-change-agent"
    parts = [
        f"## {name}: {title}" if title else f"## {name}: `{package}` SDK migration",
        "",
        f"{len(rows)} change(s) from `{rules_path}`."
        + (" Every row links to the changelog entry behind it." if rows and all(r.changelog for r in rows) else ""),
        "",
    ]
    if rows:
        parts += [render_table(rows, blob_base), ""]
    if dag_target:
        if any(r.rule_id == "airflow3-cron-timetable-pin" for r in rows):
            parts += [TIMETABLE_PIN, ""]
            parts += _timezone_section(timezone, naive_pins, blob_base)
        parts += _unpinned_section(unpinned, blob_base)
        parts += _unverified_section(unverified, blob_base, verify)
        parts += _manual_runs_section(rows, blob_base)
        if render_results:
            parts += _render_section(render_results)

    if rules or tools:
        parts.append("### Rules")
        for tool in tools:
            n = sum(1 for r in rows if r.rule_id.startswith("ruff "))
            parts.append(f"- `{tool.id}` (tool: `{tool.tool} --select {tool.select}`, "
                         f"{'applied' if n else 'not applied'}): {n} change(s), credited per rule below.")
        for rule in rules:
            mark = "applied" if rule.id in applied else "not applied"
            needs = (f"Needs `{package}>={rule.min_sdk_version}`." if rule.needs_sdk_bump
                     else f"No `{package}` bump needed.")
            extra = f" {rule_notes[rule.id]}" if rule.id in rule_notes else ""
            parts.append(f"- `{rule.id}` ({rule.fix}, {mark}): {rule.description} {needs}{extra}")
        parts.append("")

    if pin_minimum or pin_conflicts or lock_notes or dep_notes:
        parts.append("### Dependencies")
        if pin_minimum:
            parts.append(f"Pins raised to allow only `{package}>={pin_minimum}` (rows above).")
        for c in pin_conflicts:
            parts.append(f"- ⚠️ `{c.path}:{c.line}` `{c.text}`: {c.reason}")
        for n in lock_notes:
            parts.append(f"- ⚠️ `{n.path}` locks `{package}=={n.version}`. Regenerate the lock file before merging.")
        for note in dep_notes:
            parts.append(f"- ⚠️ {note}.")
        parts.append("")

    if dag_target and tests_baseline is not None:
        parts += _dag_table(tests_baseline, tests_after, dag_target, verify)

    if verify:  # the DAG checks table is the test result; nothing was changed, so "new failures" says nothing
        parts += _verify_tail(manual, skipped)
        return "\n".join(parts)

    parts.append("### Tests")
    if tests_after is None and tests_baseline is None:
        parts.append("No test command was found or tests were disabled, so **these changes are untested**.")
    else:
        ref = tests_after or tests_baseline
        parts.append(f"- Command: `{ref.command}`")
        if tests_baseline:
            parts.append(f"- Before changes: {tests_baseline.summary()}")
        if tests_after:
            parts.append(f"- After changes: {tests_after.summary()}")
            if new_failures:
                parts.append("- ❌ New failures: " + ", ".join(f"`{f}`" for f in sorted(new_failures)))
            else:
                parts.append("- No new test failures.")
        elif rows:
            parts.append("- After changes: not run, so **these changes are untested** (see Other notes).")
    parts.append("")

    if manual:
        parts.append("### Needs manual migration")
        for m in manual:
            parts.append(f"- `{m.path}:{m.line}` (`{m.rule_id}`): {m.reason}")
        parts.append("")

    if skipped_rules:
        parts.append("### Skipped rules")
        parts += [f"- {s}" for s in skipped_rules]
        parts.append("")

    if skipped or unparsable:
        parts.append("### Other notes")
        parts += [f"- {s}" for s in skipped]
        parts += [f"- `{p}`: could not be parsed, not scanned" for p in unparsable]
        parts.append("")

    if llm_notes:
        parts.append("### Claude's notes")
        parts.append("What Claude said about each patch it proposed, in full. Check the caveats before merging.")
        for n in llm_notes:
            verdict = "accepted" if n.get("accepted") else "rejected"
            parts.append(f"- `{n['path']}` (`{n['rule_id']}`, attempt {n['attempt']}, {verdict}):")
            parts += ["  > " + line if line.strip() else "  >" for line in n["notes"].splitlines()]
        parts.append("")
    if llm_usage and llm_usage.get("calls"):
        budget = f" of a ${llm_usage['budget_usd']:.2f} budget" if llm_usage.get("budget_usd") is not None else ""
        parts.append(f"<sub>Claude: {llm_usage['calls']} call(s), ${llm_usage['usd']:.4f}{budget} "
                     f"({', '.join(llm_usage['models'])}).</sub>")
        parts.append("")
    parts.append(f"<sub>Opened by {name}. It only pushes to its own branch, never to the default branch.</sub>")
    return "\n".join(parts)


def _verify_tail(manual, skipped) -> list[str]:
    out = []
    if manual:
        out += ["### Needs manual migration", *(f"- `{m.path}:{m.line}` (`{m.rule_id}`): {m.reason}" for m in manual), ""]
    if skipped:
        out += ["### Other notes", *(f"- {s}" for s in skipped), ""]
    return out + ["<sub>Airflow Migration Verifier, verify mode: nothing in either tree was changed.</sub>"]
