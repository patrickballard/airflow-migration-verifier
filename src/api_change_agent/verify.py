"""Verify-only mode: check an Airflow 3 tree migrated by anything against the Airflow 2 tree it came from.

    api-change-agent verify --before A2_TREE --after A3_TREE --venv AIRFLOW3_VENV --old-venv AIRFLOW2_VENV

Nothing is changed. It runs what the agent runs to check its own work, on someone else's:

- DagBag import of every DAG file on Airflow 3, for both trees (`--dag-test` adds dag.test());
- DAG methods Airflow 3 removed, and keyword arguments a newly imported class doesn't accept on Airflow 3;
- the render check: every template field of every DAG, old tree on Airflow 2 vs new tree on Airflow 3,
  runs paired by the moment they fire;
- cron / timedelta schedules left on Airflow 3's new default timetables ("Run dates will shift").

What the "after" tree configures is respected: `default_timezone`, and `[scheduler] create_cron_data_intervals`.
Setting that flag is the global way to keep Airflow 2's run dates, for cron *and* timedelta schedules (Airflow 3
reads it for both and never reads `create_delta_data_intervals`, apache/airflow#69868), so a tree that sets it
gets no shift warning, and its Airflow 3 render runs with it.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import NamedTuple

from . import kwarg_check, render_check
from .agent import DAG_CHECK, ManualItem, RunResult, Unverified, _rows
from .airflow_config import ENV as ENV_TZ
from .airflow_config import find_default_timezone, find_setting
from .plugins import PLUGINS, SHIFT
from .report import render_body
from .rules import load_rules
from .scanner import SKIP_DIRS, scan_repo
from .testrun import TestRunner, default_install_command

TEXT_SUFFIXES = {".py", ".sql", ".sh", ".hql", ".j2", ".jinja2", ".txt", ".cfg", ".toml", ".yaml", ".yml", ".json"}
FLAGS = (("scheduler", "create_cron_data_intervals"), ("scheduler", "create_delta_data_intervals"))


def _error_key(err: str) -> tuple[str, str]:
    """(exception class, message) of an error's first line, without module prefixes, file paths or punctuation."""
    first = next((ln for ln in err.strip().splitlines() if ln.strip()), "")
    first = re.sub(r"\s*\((?:/|[A-Za-z]:\\)[^)]*\)", "", first)  # "(/path/to/site-packages/...)"
    cls, _, msg = first.partition(":")
    return cls.rsplit(".", 1)[-1].strip(), re.sub(r"[\"'`]", "", msg).strip().rstrip(".")


def environment_only(airflow2_error: str, airflow3_error: str) -> bool:
    """A DAG file failed on both versions: is its Airflow 3 failure just the Airflow 2 one again (a package,
    Variable or connection the environment lacks), or an Airflow 3 break that happens before it?"""
    if _error_key(airflow2_error) == _error_key(airflow3_error):
        return True
    cls, msg = _error_key(airflow3_error)
    # "airflow" anywhere, not as a word: AirflowException (an argument Airflow 3 rejects) is Airflow's own
    return not re.search(r"airflow|\bDAG\b", f"{cls}: {msg}", re.I)


def _constraints(url: str) -> str:
    """pip -c argument; `{python}` becomes the venv's X.Y, as in the agent's own installs."""
    py = "$(python -c 'import sys; print(\"%d.%d\" % sys.version_info[:2])')"
    return f' -c "{url.replace("{python}", py)}"' if url else ""


def install_commands(before: Path, after: Path, ruleset) -> tuple[str, str]:
    """(Airflow 3 venv, Airflow 2 venv) install commands, from what each tree declares."""
    import re

    target = str(ruleset.target_version)
    new_c = ruleset.install_constraints.replace("{version}", target)
    new = (f"python -m pip install -q --upgrade pip && python -m pip install -q '{ruleset.dist}=={target}'"
           f"{_constraints(new_c)} && " + default_install_command(after, None, new_c))
    req = Path(before) / "requirements.txt"
    pin = re.search(r"^\s*apache[-_.]airflow(?:\[[^\]]*\])?==([\w.]+)", req.read_text(errors="replace"), re.M | re.I) \
        if req.exists() else None
    version = pin.group(1) if pin else str(ruleset.source_version)
    old_c = ruleset.install_constraints.replace("{version}", version)
    old = (f"python -m pip install -q --upgrade pip && python -m pip install -q '{ruleset.dist}=={version}'"
           f"{_constraints(old_c)} && " + default_install_command(before, None, old_c))
    return new, old


def build_venvs(before: Path, after: Path, ruleset, venv: Path, log=print) -> tuple[Path, Path]:
    """The Airflow 3 venv at `venv`, the Airflow 2 one next to it (`<venv>-airflow2`): the same places the
    agent uses, so a workflow's actions/cache keeps both."""
    new, old = install_commands(before, after, ruleset)
    old_venv = venv.with_name(venv.name + "-airflow2")
    log(f"verify: Airflow {ruleset.target_version} venv at {venv}")
    TestRunner(after, "true", new, venv, log=log).setup()
    log(f"verify: Airflow 2 venv at {old_venv}")
    TestRunner(before, "true", old, old_venv, log=log).setup()
    return venv, old_venv


class Problem(NamedTuple):
    path: str  # relative to the tree, so it matches the PR's diff
    line: int | None
    text: str


def strict_problems(summary: dict) -> list[Problem]:
    """What --strict fails on: a DAG that doesn't import on Airflow 3 or renders differently, a removed DAG
    method, a removed context key or template variable, a rejected kwarg."""
    dags = summary["dags"].items()
    out = [Problem(k.split("::")[0], None, f"{k}: does not import on Airflow 3") for k, d in dags
           if not d["imports"] and not d.get("fails_on_airflow2")]
    out += [Problem(k.split("::")[0], None, f"{k}: renders differently from Airflow 2")
            for k, d in dags if d["render"] == "mismatch"]
    out += [Problem(m["path"], m["line"], m["reason"]) for m in summary["manual"]
            if m["rule_id"] in ("kwarg-check", "airflow3-removed-dag-methods", "airflow3-context-keys",
                                "airflow3-templates")]
    return out


def annotation(p: Problem) -> str:
    """A GitHub `::error` workflow command, so the check annotates the file (and line) in the PR."""
    def esc(v: str, prop: bool = False) -> str:
        v = v.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        return v.replace(":", "%3A").replace(",", "%2C") if prop else v
    where = f"file={esc(p.path, True)}" + (f",line={p.line}" if p.line else "")
    msg = p.text if p.line is None else f"{p.path}:{p.line}: {p.text}"
    return f"::error {where},title=Airflow 3 migration::{esc(msg)}"


def _text_files(root: Path) -> dict[str, str]:
    out = {}
    for p in root.rglob("*"):
        if not p.is_file() or set(p.relative_to(root).parts) & (SKIP_DIRS | {".git"}) or p.suffix not in TEXT_SUFFIXES:
            continue
        try:
            if p.stat().st_size < 1_000_000:
                out[p.relative_to(root).as_posix()] = p.read_text(errors="replace")
        except OSError:
            continue
    return out


def _dag_check(tree: Path, venv: Path, dag_test: bool, timeout: int, log):
    cmd = f"python {shlex.quote(str(DAG_CHECK))} --repo . --junitxml {{junit}} --timeout {timeout}"
    return TestRunner(tree, cmd + ("" if dag_test else " --import-only"), None, venv, log=log).run()


def verify(before: Path, after: Path, *, rules_path: Path, venv: Path, old_venv: Path | None,
           dag_test: bool = False, timeout: int = 45, log=print) -> tuple[RunResult, dict]:
    before, after = Path(before).resolve(), Path(after).resolve()
    ruleset = load_rules(rules_path)
    res = RunResult()
    target = f"{ruleset.dist} {ruleset.target_version}"

    # what the after tree configures
    tz_before, tz_after = find_default_timezone(before), find_default_timezone(after)
    flags = {key: find_setting(after, section, key) for section, key in FLAGS}
    env_after = {ENV_TZ: tz_after.tz} if tz_after.tz else {}
    env_after.update({s.env: s.value for s in flags.values() if s.value is not None})
    for s in flags.values():
        if s.value is None:
            continue
        if s.key == "create_delta_data_intervals":
            res.skipped.append(f"the after tree sets `[{s.section}] {s.key} = {s.value}` ({s.where()}), but Airflow 3 "
                               "never reads it (apache/airflow#69868, checked on 3.3.2): "
                               "`create_cron_data_intervals` decides timedelta schedules too")
        else:
            res.skipped.append(f"the after tree sets `[{s.section}] {s.key} = {s.value}` ({s.where()}); "
                               "its Airflow 3 render ran with it")

    # what changed
    old_text, new_text = _text_files(before), _text_files(after)
    rows = []
    for rel in sorted(set(old_text) | set(new_text)):
        a, b = old_text.get(rel, ""), new_text.get(rel, "")
        if a != b:
            rows += _rows(Path(rel), a, b, "external change", "", "")
    res.changed_files = sorted(r for r in set(old_text) | set(new_text) if old_text.get(r) != new_text.get(r))

    # 1. DAG import on Airflow 3, before and after
    log("DAG check: before tree")
    res.tests_baseline = _dag_check(before, venv, dag_test, timeout, log)
    log("DAG check: after tree")
    res.tests_after = _dag_check(after, venv, dag_test, timeout, log)
    # a file that doesn't import on Airflow 2 either, the same way, fails for a reason of its own (a package,
    # Variable or connection this environment doesn't have), not because of Airflow 3: it isn't counted
    on_airflow2 = _dag_check(before, old_venv, False, timeout, log) if old_venv else None
    env_failures = {}
    imports_after = {i.split("::")[0] for i in res.tests_after.ids if i.endswith("::import")}
    for f in sorted(imports_after):
        if f"{f}::import" in res.tests_after.failures:
            err = res.tests_after.messages.get(f"{f}::import", "import failed")
            if on_airflow2 and f"{f}::import" in on_airflow2.failures and environment_only(
                    on_airflow2.messages.get(f"{f}::import", ""), err):
                env_failures[f] = on_airflow2.messages.get(f"{f}::import", "import failed")
            else:
                res.unverified.append(Unverified(f, err))
    for f, err in env_failures.items():
        res.skipped.append(f"`{f}` doesn't import on Airflow 2 either, so it isn't counted as an Airflow 3 problem: "
                           f"`{err.splitlines()[0][:200] if err else 'import failed'}`. Usually a package, Variable or "
                           "connection the check's environment doesn't have.")

    # 2. removed DAG methods, rejected kwargs
    rules = {r.id: r for r in ruleset.rules}
    files = [rel.as_posix() for rel in scan_repo(after, ruleset.package).files]
    methods = rules.get("airflow3-removed-dag-methods")
    for rel in files:
        text = new_text.get(rel) or (after / rel).read_text(errors="replace")
        if methods:
            for site in PLUGINS["airflow_removed_dag_methods"].find(Path(rel), text, methods.options, "llm"):
                res.manual.append(ManualItem(rel, site.line, methods.id, site.reason))
    # removed context keys / template variables still in use: import fine, fail when the task runs
    for rule_id, plugin, what in (("airflow3-context-keys", "airflow_context_keys", "a task-context key"),
                                  ("airflow3-templates", "airflow_templates", "a template variable")):
        rule = rules.get(rule_id)
        if not rule:
            continue
        for rel, text in sorted(new_text.items()):
            try:
                det = PLUGINS[plugin].find(Path(rel), text, rule.options, "deterministic")
                judgment = PLUGINS[plugin].find(Path(rel), text, rule.options, "llm")
            except Exception:  # noqa: BLE001 - unparsable: the DAG check reports it
                continue
            fixed = []
            if det:  # the rewrite migrate mode would make, line by line, so the reader can make it by hand
                try:
                    fixed = PLUGINS[plugin].apply(Path(rel), text, rule.options).splitlines()
                except Exception:  # noqa: BLE001
                    fixed = []
                if len(fixed) != len(text.splitlines()):
                    fixed = []
            for s in det:
                fix = (f"; with its documented equivalent, write the line as `{fixed[s.line - 1].strip()}`"
                       if fixed and fixed[s.line - 1] != text.splitlines()[s.line - 1] else
                       "; migrate mode rewrites it to the documented equivalent")
                res.manual.append(ManualItem(rel, s.line, rule_id, f"`{s.snippet}` uses {what} Airflow 3 removed{fix}"))
            for s in judgment:
                why = s.reason if s.reason.startswith("`") else f"`{s.snippet}`: {s.reason}"
                res.manual.append(ManualItem(rel, s.line, rule_id, f"uses {what} Airflow 3 removed. {why}"))
    swapped = {}
    for rel in files:
        if rel in old_text and rel in new_text:
            try:
                found = kwarg_check.introduced_classes(old_text[rel], new_text[rel])
            except Exception:  # noqa: BLE001 - unparsable: the DAG check reports it
                continue
            if found:
                swapped[rel] = found
    if swapped:
        sigs = kwarg_check.signatures(Path(venv) / "bin" / "python",
                                      sorted({p for f in swapped.values() for p, _ in f.values()}))
        for rel, found in swapped.items():
            for p in kwarg_check.problems(new_text[rel], found, sigs):
                res.manual.append(ManualItem(rel, p.line, "kwarg-check", p.reason))
        unread = sorted(k for k, v in sigs.items() if "error" in v)
        if unread:
            res.skipped.append(f"kwarg check couldn't read these classes on the target: {', '.join(unread)}")

    # 3. schedules left on Airflow 3's new defaults (unless the tree restores the old ones globally)
    pin = rules.get("airflow3-cron-timetable-pin")
    if pin:
        opts = {**pin.options, "default_timezone": tz_after.tz or "UTC", "timezone_problem": tz_after.problem}
        for rel in files:
            text = new_text.get(rel) or (after / rel).read_text(errors="replace")
            try:
                sites = (PLUGINS["airflow_cron_pin"].find(Path(rel), text, opts, "deterministic")
                         + PLUGINS["airflow_cron_pin"].find(Path(rel), text, opts, "llm"))
            except Exception:  # noqa: BLE001
                continue
            for s in sites:
                # Airflow 3 picks cron *and* timedelta timetables from this one flag (apache/airflow#69868)
                if flags["create_cron_data_intervals"].is_true():
                    continue
                why = s.reason if s.reason.startswith(SHIFT) else (
                    f"{SHIFT}: a cron schedule left on Airflow 3's default timetable; pin it, or set "
                    "[scheduler] create_cron_data_intervals = True")
                res.unpinned.append({"path": rel, "line": s.line, "schedule": s.snippet, "reason": why, "evidence": ""})

    # 4. render check, every field, paired by fire time
    if old_venv:
        both = sorted(set(files) & {rel.as_posix() for rel in scan_repo(before, ruleset.package).files})
        try:
            old = render_check.render(Path(old_venv) / "bin" / "python", before, both,
                                      env={ENV_TZ: tz_before.tz} if tz_before.tz else {})
            new = render_check.render(Path(venv) / "bin" / "python", after, both, env=env_after)
            res.render_results = render_check.compare(old, new, changed=set(), whole_dags=set(new["dags"]))
        except Exception as e:  # noqa: BLE001
            res.skipped.append(f"render check failed to run: {e}")
            old = new = {"dags": {}}
    else:
        res.skipped.append("render check not run: pass --old-venv (an Airflow 2 venv)")
        old = new = {"dags": {}}
    for u in res.unpinned:
        bad = [r for r in res.render_results if r.file == u["path"] and r.mismatches]
        if bad:
            u["evidence"] = f"{render_check.summary(bad[0])} (field {bad[0].dag_id}.{bad[0].task_id}.{bad[0].field})"

    res.rows = rows
    res.body = render_body(
        package=ruleset.dist, rows=rows, rules=[], pin_minimum=None, pin_conflicts=[], lock_notes=[],
        skipped_rules=[], skipped=res.skipped, manual=res.manual, unparsable=[], tests_baseline=res.tests_baseline,
        tests_after=res.tests_after, new_failures=set(), blob_base=None, rules_path=f"{before.name}/` and `{after.name}/",
        title=f"verify an Airflow 2 → 3 change ({target})", dag_target=target,
        render_results=res.render_results, unverified=res.unverified, unpinned=res.unpinned, timezone=tz_after,
        verify=True)
    return res, _per_dag(res, old, new, set(env_failures))


def _per_dag(res: RunResult, old: dict, new: dict, env_failures: set = frozenset()) -> dict:
    """One verdict per DAG: does it import on Airflow 3, and how did its templates render."""
    out = {}
    after = res.tests_after
    for i in after.ids:
        if "::dag:" in i or not i.endswith("::import"):
            continue
        f = i.split("::")[0]
        # a file that fails on Airflow 3 has no DAG ids there: name its DAGs as Airflow 2 does
        keys = ([k for k, d in new["dags"].items() if d["file"] == f]
                or [k for k, d in old["dags"].items() if d["file"] == f] or [f"{f}::?"])
        for key in keys:
            fields = [r for r in res.render_results if f"{r.file}::{r.dag_id}" == key]
            if i in after.failures:
                render = "not reached"
            elif any(r.mismatches for r in fields):
                render = "mismatch"
            elif any(not r.runs for r in fields):
                render = "not render-checked"
            elif fields:
                render = "identical"
            else:
                render = "no template fields" if key in new["dags"] else "not rendered"
            out[key] = {"imports": i not in after.failures, "render": render, "fails_on_airflow2": f in env_failures,
                        "fields": len(fields), "mismatched_fields": sum(1 for r in fields if r.mismatches),
                        "example": next((f"{r.task_id}.{r.field}: {render_check.summary(r)}" for r in fields
                                         if r.mismatches), "")}
    return out


def main(argv) -> int:
    import argparse

    from .cli import BUNDLED_RULES

    p = argparse.ArgumentParser(prog="api-change-agent verify", description=__doc__.split("\n\n")[0])
    p.add_argument("--before", required=True, help="the Airflow 2 tree")
    p.add_argument("--after", required=True, help="the same tree after migration to Airflow 3")
    p.add_argument("--rules", default="airflow")
    p.add_argument("--venv", help="an Airflow 3 venv (DAG import, render, signatures); built with --install-command auto")
    p.add_argument("--old-venv", help="an Airflow 2 venv (render of the before tree); default <venv>-airflow2 when built")
    p.add_argument("--install-command", choices=["auto"], help="build both venvs from what each tree declares")
    p.add_argument("--dag-test", action="store_true", help="also run dag.test() per DAG (slow)")
    p.add_argument("--strict", action="store_true",
                   help="exit 1 if a DAG doesn't import on Airflow 3 or renders differently, or a removed DAG "
                        "method or rejected kwarg is used")
    p.add_argument("--output", help="write report.md and result.json here")
    a = p.parse_args(argv)
    rules = Path(a.rules)
    if not rules.is_file():  # a bundled ruleset by name, even next to an airflow/ folder
        rules = BUNDLED_RULES.parent / f"{a.rules}.yaml"
    venv = Path(a.venv) if a.venv else None
    old_venv = Path(a.old_venv) if a.old_venv else None
    if a.install_command == "auto":
        venv, old_venv = build_venvs(Path(a.before), Path(a.after), load_rules(rules),
                                     venv or Path("/tmp/aca-verify-venv"))
    if venv is None:
        p.error("pass --venv (an Airflow 3 venv) or --install-command auto")
    res, dags = verify(Path(a.before), Path(a.after), rules_path=rules, venv=venv, old_venv=old_venv,
                       dag_test=a.dag_test)
    print(res.body)
    summary = res.summary()
    summary["dags"] = dags
    if a.output:
        out = Path(a.output)
        out.mkdir(parents=True, exist_ok=True)
        (out / "report.md").write_text(res.body + "\n")
        (out / "result.json").write_text(json.dumps(summary, indent=2, default=str) + "\n")
    import os

    if step := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step, "a") as f:
            f.write(res.body + "\n")
    problems = strict_problems(summary) if a.strict else []
    for problem in problems:
        print(annotation(problem))
    return 1 if problems else 0
