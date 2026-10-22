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
        out.append(f"| {loc} | {change} | `{r.rule_id}` | [changelog]({r.changelog}) |")
    return "\n".join(out)


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


def _dag_table(before, after, target) -> list[str]:
    ids = list(dict.fromkeys([*before.ids, *(after.ids if after else ())]))
    lines = [f"### DAG checks on {target}", "",
             "Each DAG file is imported (DagBag) and each DAG run with `dag.test()`, before and after this PR.", "",
             "| Check | Before | After |", "|---|---|---|"]
    for test_id in ids:
        lines.append(f"| `{test_id}` | {_dag_status(before, test_id)} | {_dag_status(after, test_id)} |")
    return lines + [""]


def render_body(*, package, rows, rules, pin_minimum, pin_conflicts, lock_notes, skipped_rules, skipped, manual,
                unparsable, tests_baseline, tests_after, new_failures, blob_base, rules_path,
                rule_notes=None, dep_notes=(), title="", tools=(), dag_target=None) -> str:
    rule_notes = rule_notes or {}
    applied = sorted({r.rule_id for r in rows})
    parts = [
        f"## api-change-agent: {title}" if title else f"## api-change-agent: `{package}` SDK migration",
        "",
        f"{len(rows)} change(s) from `{rules_path}`. Every row links to the changelog entry behind it.",
        "",
    ]
    if rows:
        parts += [render_table(rows, blob_base), ""]

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
        parts += _dag_table(tests_baseline, tests_after, dag_target)

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

    parts.append("<sub>Opened by api-change-agent. It only pushes to its own branch, never to the default branch.</sub>")
    return "\n".join(parts)
