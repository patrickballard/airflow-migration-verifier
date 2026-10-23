"""Orchestration: scan -> deterministic fixes -> pins -> LLM fixes -> tests -> PR."""
from __future__ import annotations

import dataclasses
import difflib
import fnmatch
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import libcst as cst
from packaging.requirements import Requirement
from packaging.version import Version

from . import gitops, render_check, ruff_layer
from .airflow_config import ENV as ENV_TZ
from .airflow_config import find_default_timezone
from .codemod import Site, apply_deterministic, find_sites, missing_comments
from .deps import plan_add_dependency
from .llm import BudgetExhausted, PatchModel, PatchRequest
from .pins import PinPlan, apply_pins, plan_pins
from .plugins import NAIVE, PLUGINS
from .report import Row, render_body
from .rules import Rule, load_rules
from .scanner import SKIP_DIRS, iter_python_files, scan_repo
from .testrun import (TestOutcome, TestRunner, default_install_command, detect_test_command, merge_outcome,
                      new_failures)

DEFAULT_BRANCH = "api-change-agent/openai"
DAG_CHECK = Path(__file__).with_name("airflow_dag_check.py")


@dataclass
class Options:
    repo: Path
    rules_path: Path
    dry_run: bool = False
    test_command: str | None = "auto"  # None or "" disables tests
    install_command: str | None = None  # "auto" builds one from the repo's files
    venv_dir: Path | None = None
    venv_python: str | None = None  # interpreter for the test venv (default: this one)
    render_old_venv: Path | None = None  # Airflow 2 venv for the render check (built if install is "auto")
    max_llm_attempts: int = 2
    branch: str = DEFAULT_BRANCH
    base_branch: str | None = None
    default_branch: str | None = None
    repo_slug: str | None = None
    server_url: str = "https://github.com"
    exclude: tuple[str, ...] = ()
    log: object = print


@dataclass(frozen=True)
class ManualItem:
    path: str
    line: int
    rule_id: str
    reason: str


@dataclass(frozen=True)
class Unverified:
    """A file that fails on the target before and after the change: nothing in it could be checked."""
    path: str
    error: str


@dataclass
class RunResult:
    rows: list[Row] = field(default_factory=list)
    manual: list[ManualItem] = field(default_factory=list)
    skipped_rules: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # other notes (tests, ...)
    unparsable: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    pin_plan: PinPlan | None = None
    tests_baseline: TestOutcome | None = None
    tests_after: TestOutcome | None = None
    new_failures: set[str] = field(default_factory=set)
    rule_notes: dict[str, str] = field(default_factory=dict)  # rule id -> why it did/didn't apply
    llm_usage: dict = field(default_factory=dict)  # calls, tokens and USD spent on Claude
    llm_notes: list = field(default_factory=list)  # Claude's full notes per attempt (never truncated)
    render_results: list = field(default_factory=list)  # Airflow render check, one per template field
    unverified: list[Unverified] = field(default_factory=list)
    unpinned: list = field(default_factory=list)  # Airflow schedules left on the new timetable defaults
    dep_notes: list[str] = field(default_factory=list)
    pushed: bool = False
    pr_url: str | None = None
    body: str = ""
    diff: str = ""  # unified diff of everything the run changed

    def summary(self) -> dict:
        """JSON-friendly record of the run (what --output writes as result.json)."""
        def tests(t):
            return None if t is None else {"command": t.command, "passed": t.passed, "returncode": t.returncode,
                                           "failures": sorted(t.failures), "summary": t.summary(),
                                           "ids": list(t.ids), "messages": dict(t.messages)}
        return {
            "rules_fired": sorted({r.rule_id for r in self.rows}),
            "rows": [dataclasses.asdict(r) for r in self.rows],
            "changed_files": self.changed_files,
            "manual": [dataclasses.asdict(m) for m in self.manual],
            "skipped_rules": self.skipped_rules,
            "other_notes": self.skipped,
            "rule_notes": self.rule_notes,
            "unparsable": self.unparsable,
            "tests_before": tests(self.tests_baseline),
            "tests_after": tests(self.tests_after),
            "new_failures": sorted(self.new_failures),
            "llm_usage": self.llm_usage,
            "llm_notes": self.llm_notes,
            "render": [dataclasses.asdict(r) for r in self.render_results],
            "unverified": [dataclasses.asdict(u) for u in self.unverified],
            "unpinned": self.unpinned,
        }


# --------------------------------------------------------------------------
# line bookkeeping


WHOLE_DAG = ("airflow_cron_pin", "airflow_days_ago")  # changes that answer for every template field of a DAG


def _pin_can_explain(res) -> bool:
    """A timetable changes dates, never which names exist: undefined-variable errors aren't the pin's doing."""
    return any("RENDER ERROR UndefinedError" not in m.new for m in res.mismatches) or not res.mismatches


def _rows(rel: Path, old: str, new: str, rule_id: str, changelog: str, note: str) -> list[Row]:
    """One Row per changed hunk, keeping the exact source lines for a later revert."""
    a, b = old.splitlines(), new.splitlines()
    rows = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            before, after = "\n".join(a[i1:i2]), "\n".join(b[j1:j2])
            rows.append(Row(rel.as_posix(), j1 + 1, textwrap.dedent(before), textwrap.dedent(after), rule_id,
                            changelog, note, before, after))
    return rows


def _line_map(old: str, new: str):
    a, b = old.splitlines(), new.splitlines()
    mapping: dict[int, int] = {}
    for tag, i1, i2, j1, _ in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        for k in range(i1, i2):
            mapping[k + 1] = (j1 + (k - i1) if tag == "equal" else j1) + 1
    return lambda line: mapping.get(line, line)


class _Run:
    def __init__(self, options: Options, model: PatchModel | None, github):
        self.o = options
        self.repo = Path(options.repo).resolve()
        self.model = model
        self.github = github
        self.log = options.log if callable(options.log) else print
        self.ruleset = load_rules(options.rules_path)
        self.result = RunResult()
        self.originals: dict[Path, str] = {}  # every file we may touch, as first read
        self.current: dict[Path, str] = {}
        self.rows: dict[Path, list[Row]] = {}
        self.runner: TestRunner | None = None
        self.state_outcome: TestOutcome | None = None
        self.applied_rules: list[Rule] = []
        self.tested_after_changes = False
        self.tests_unusable = False
        self.pin_minimum: Version | None = None
        self.inserted: list[tuple[Path, int]] = []  # (file, line) of whole lines added by dependency rules
        self.tz = find_default_timezone(self.repo) if self.ruleset.verify == "airflow-dags" else None
        self.render_reverted: dict[Path, set[str]] = {}  # rule ids the render check took back, per file

    # -- helpers ------------------------------------------------------------
    def _write(self, rel: Path, text: str) -> None:
        self.originals.setdefault(rel, (self.repo / rel).read_text())
        (self.repo / rel).write_text(text)

    def _set_source(self, rel: Path, new: str, new_rows: list[Row]) -> None:
        remap = _line_map(self.current[rel], new)
        self.rows[rel] = [dataclasses.replace(r, line=remap(r.line)) for r in self.rows.get(rel, [])] + new_rows
        self.current[rel] = new
        self._write(rel, new)

    def _pin_blocker(self, rule: Rule) -> str | None:
        """Why the pins can't be raised to this rule's minimum, if they can't."""
        plan = plan_pins(self.repo, self.ruleset.dist, rule.min_sdk_version)
        if plan.holds:
            where = ", ".join(f"`{h.path}:{h.line}` `{h.text}` (`{h.comment}`)" for h in plan.holds)
            return (f"`{rule.id}`: needs `{self.ruleset.dist}>={rule.min_sdk_version}`, but {where} "
                    "is marked as intentional, so the pin was left alone and the rule was not applied")
        if plan.conflicts:
            where = ", ".join(f"`{c.path}:{c.line}` `{c.text}`" for c in plan.conflicts)
            return (f"`{rule.id}`: {where} can't be raised to >={rule.min_sdk_version} "
                    f"({plan.conflicts[0].reason})")
        return None

    def _options(self, rule: Rule) -> dict:
        if rule.match.plugin != "airflow_cron_pin" or self.tz is None:
            return rule.options
        tz = self.tz.tz or (None if self.tz.problem else "UTC")  # nothing set: Airflow's default, UTC
        return {**rule.options, "default_timezone": tz, "timezone_problem": self.tz.problem}

    def naive_pin_lines(self) -> set[tuple[Path, int]]:
        """Current (file, line) of pinned schedules whose DAG has a naive start_date (zone = default_timezone)."""
        rule = next((r for r in self.ruleset.rules if r.match.plugin == "airflow_cron_pin"), None)
        if rule is None:
            return set()
        out = set()
        for rel, text in self.originals.items():
            if rel.suffix != ".py" or rel not in self.current:
                continue
            remap = _line_map(text, self.current[rel])
            out |= {(rel, remap(s.line)) for s in PLUGINS[rule.match.plugin].find(rel, text, self._options(rule),
                                                                                 "deterministic")
                    if s.reason.startswith(NAIVE)}
        return out

    def _render_env(self) -> dict:
        return {ENV_TZ: self.tz.tz} if self.tz and self.tz.tz else {}

    def _sites(self, text: str, rule: Rule, rel: Path) -> list[Site]:
        if rule.match.plugin:
            mode = "deterministic" if rule.fix == "deterministic" else "llm"
            return PLUGINS[rule.match.plugin].find(rel, text, self._options(rule), mode)
        return find_sites(text, rule)

    def _template_files(self) -> list[Path]:
        globs = {g for r in self.ruleset.rules if r.match.plugin for g in PLUGINS[r.match.plugin].file_globs}
        found = []
        for dirpath, dirnames, filenames in os.walk(self.repo):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                rel = (Path(dirpath) / name).relative_to(self.repo)
                if any(fnmatch.fnmatch(name, g) for g in globs) and not any(
                        fnmatch.fnmatch(rel.as_posix(), pat) for pat in self.o.exclude):
                    found.append(rel)
        return found

    def _run_tests(self, changed: list[Path] | None = None) -> TestOutcome | None:
        """Run the repo's checks. With `changed`, the Airflow DAG check re-tests only those DAG files and
        reuses the last results for the rest, when nothing else can be affected (see _alone)."""
        if not self.runner:
            return None
        if changed and self._alone(changed):
            files = [rel.as_posix() for rel in changed]
            self.log(f"test: re-checking only {', '.join(files)}")
            return merge_outcome(self.state_outcome, self.runner.run(("--files", *files)), files)
        return self.runner.run()

    def _alone(self, changed: list[Path]) -> bool:
        """Whether re-checking just these files is enough: they are DAG files the last check covered, and
        no other file imports them (a helper module's change can break every DAG that uses it)."""
        last = self.state_outcome
        if (self.ruleset.verify != "airflow-dags" or "airflow_dag_check" not in self.runner.command
                or last is None or not last.has_junit):
            return False
        for rel in changed:
            if f"{rel.as_posix()}::import" not in last.ids:
                return False
            imports_it = re.compile(rf"^\s*(from\s+[\w.]*\b{re.escape(rel.stem)}\b[\w.]*\s+import|import\s+.*\b"
                                    rf"{re.escape(rel.stem)}\b)", re.M)
            for other in iter_python_files(self.repo, self.o.exclude):
                if other != rel and imports_it.search((self.repo / other).read_text(errors="replace")):
                    return False
        return True

    # -- stages ---------------------------------------------------------------
    def scan(self) -> dict[str, dict[Path, list[Site]]]:
        scan = scan_repo(self.repo, self.ruleset.package, self.o.exclude)
        self.result.unparsable = [p.as_posix() for p in scan.unparsable]
        hits: dict[str, dict[Path, list[Site]]] = {}
        for rel in scan.files:
            self.current[rel] = (self.repo / rel).read_text()
            for rule in self.ruleset.rules:
                if rule.match.module:
                    continue
                sites = self._sites(self.current[rel], rule, rel)
                if sites:
                    hits.setdefault(rule.id, {})[rel] = sites
                    self.originals.setdefault(rel, self.current[rel])
        module_rules = [r for r in self.ruleset.rules if r.match.module]
        if module_rules:
            for rel in iter_python_files(self.repo, self.o.exclude):
                try:
                    text = (self.repo / rel).read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for rule in module_rules:
                    if rule.match.module.split(".")[0] not in text:
                        continue
                    try:
                        sites = find_sites(text, rule)
                    except cst.ParserSyntaxError:
                        continue
                    if sites:
                        hits.setdefault(rule.id, {})[rel] = sites
        self.sdk_files = list(scan.files)
        if scan.files and any(r.match.plugin for r in self.ruleset.rules):
            for rel in self._template_files():
                try:
                    text = (self.repo / rel).read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for rule in self.ruleset.rules:
                    if rule.match.plugin and (sites := self._sites(text, rule, rel)):
                        hits.setdefault(rule.id, {})[rel] = sites
                        self.current.setdefault(rel, text)
                        self.originals.setdefault(rel, text)
        for tool in self.ruleset.tools:
            diags = ruff_layer.diagnose(self.repo, [f.as_posix() for f in scan.files], tool)
            for d in diags:
                hits.setdefault(tool.id, {}).setdefault(Path(d.path), []).append(Site(d.line, f"{d.code}: {d.message}"))
            for rel in hits.get(tool.id, {}):
                self.originals.setdefault(rel, self.current[rel])
        found = ", ".join(f"{rid}: {sum(map(len, f.values()))} site(s)" for rid, f in hits.items())
        self.log(f"scanned {len(scan.files)} file(s) importing {self.ruleset.package}; {found or 'no hits'}")
        return hits

    def bump_to_target(self) -> None:
        """Raise the pins to the ruleset's target first, so the baseline is old code on the new major."""
        target = self.ruleset.target_version
        plan = plan_pins(self.repo, self.ruleset.dist, target)
        if plan.holds or plan.conflicts:
            where = ", ".join(f"`{x.path}:{x.line}` `{x.text}`" for x in (*plan.holds, *plan.conflicts))
            self.result.skipped_rules.append(
                f"upgrade to `{self.ruleset.dist}` {target}: {where} can't be raised "
                f"({'marked as intentional' if plan.holds else plan.conflicts[0].reason})")
            self.result.pin_plan = plan
            return
        for rel in plan.files:
            self.originals.setdefault(rel, (self.repo / rel).read_text())
        apply_pins(self.repo, plan)
        self.result.pin_plan = plan
        self.pin_minimum = target

    def setup_tests(self) -> None:
        command = self.o.test_command
        if command == "auto":
            if self.ruleset.verify == "airflow-dags":
                command = f"python {shlex.quote(str(DAG_CHECK))} --repo . --junitxml {{junit}}"
            else:
                command = detect_test_command(self.repo)
        if not command:
            return
        install = self.o.install_command
        if install == "auto":
            constraints = self.ruleset.install_constraints
            if constraints and self.ruleset.target_version:
                constraints = constraints.replace("{version}", str(self.ruleset.target_version))
            install = default_install_command(self.repo, command, constraints)
        self.runner = TestRunner(self.repo, command, install, self.o.venv_dir, log=self.log,
                                 venv_python=self.o.venv_python)
        try:
            self.runner.setup()
        except Exception as e:  # noqa: BLE001 - reported in the PR instead
            self.result.skipped.append(f"tests not run: {e}")
            self.runner = None
            return
        self.result.tests_baseline = self.state_outcome = self.runner.run()
        self.log(f"baseline tests: {self.state_outcome.summary()}")
        if not self.state_outcome.passed and not self.state_outcome.has_junit:
            # No per-test results and a red baseline: we can't tell a regression apart.
            self.result.skipped.append(
                f"test gate disabled: `{command}` already fails before any change "
                f"(exit {self.state_outcome.returncode}) and produced no per-test results"
            )
            self.runner = None
            self.tests_unusable = True

    def tools(self, hits) -> list:
        applied = []
        for tool in self.ruleset.tools:
            if tool.id not in hits:
                continue
            res = ruff_layer.run(self.repo, [f.as_posix() for f in self.sdk_files], tool)
            self._record_ruff(res, tool)
            if res.changes:
                applied.append(tool)
        return applied

    def _record_ruff(self, res, tool, left_reason=None) -> None:
        by_file: dict[Path, list[Row]] = {}
        for c in res.changes:
            codes = "/".join(c.codes) or tool.select
            note = f"fixed by ruff {', '.join(c.codes)}: " + " ".join(c.messages)
            by_file.setdefault(Path(c.path), []).append(
                Row(c.path, c.line, c.before, c.after, f"ruff {codes}", c.url or tool.changelog, note))
        for rel, text in res.new_text.items():
            # ruff already wrote the file; this records it (self.current still holds the pre-ruff text)
            self._set_source(Path(rel), text, by_file.get(Path(rel), []))
        for d in res.left:
            why = (left_reason(d) if left_reason else None) or f"ruff {d.code} has no automatic fix: {d.message}"
            self.result.manual.append(ManualItem(d.path, d.line, f"ruff {d.code}", why))

    def _refresh_ruff_manual(self) -> None:
        """Drop ruff's "no automatic fix" items that a later rule fixed after all (e.g. days_ago)."""
        if not self.ruleset.tools or not any(m.rule_id.startswith("ruff ") for m in self.result.manual):
            return
        left: Counter = Counter()
        for tool in self.ruleset.tools:
            for d in ruff_layer.diagnose(self.repo, [f.as_posix() for f in self.sdk_files], tool):
                left[(d.path, f"ruff {d.code}", d.message)] += 1
        kept = []
        for m in self.result.manual:
            if m.rule_id.startswith("ruff "):
                key = next((k for k in left if k[:2] == (m.path, m.rule_id) and m.reason.endswith(k[2]) and left[k]),
                           None)
                if key is None:
                    continue
                left[key] -= 1
            kept.append(m)
        self.result.manual = kept

    def deterministic(self, hits) -> None:
        stage_rules = self.tools(hits)
        for rule in self.ruleset.rules:
            if rule.fix != "deterministic" or rule.id not in hits or "add_dependency" in rule.transform:
                continue
            if reason := self._pin_blocker(rule):
                self.result.skipped_rules.append(reason)
                continue
            for rel in hits[rule.id]:
                if rule.match.plugin:
                    new = PLUGINS[rule.match.plugin].apply(rel, self.current[rel], self._options(rule))
                    rows = _rows(rel, self.current[rel], new, rule.id, rule.changelog, rule.note)
                else:
                    new, edits = apply_deterministic(self.current[rel], rule)
                    rows = [Row(rel.as_posix(), e.line, e.before, e.after, rule.id, rule.changelog) for e in edits]
                if new != self.current[rel]:
                    self._set_source(rel, new, rows)
            stage_rules.append(rule)
        if not stage_rules:
            return
        self.applied_rules += [r for r in stage_rules if isinstance(r, Rule)]
        self.bump_pins()
        if self.runner:
            outcome = self._run_tests()
            regressions = new_failures(self.state_outcome, outcome)
            if regressions:
                self._revert_all(stage_rules, f"repo tests failed after the rewrite: {', '.join(sorted(regressions))}",
                                 hits)
                return
            self.state_outcome = outcome
            self.tested_after_changes = True
        self.render_gate()

    # -- render check (Airflow) ------------------------------------------------
    def _plugin_of(self, rule_id: str) -> str | None:
        rule = next((r for r in self.ruleset.rules if r.id == rule_id), None)
        return rule.match.plugin if rule else None

    def _render_pythons(self):
        if self.ruleset.verify != "airflow-dags":
            return None
        if not hasattr(self, "_render_py"):
            self._render_py = None
            new = self.o.venv_dir / "bin" / "python" if self.o.venv_dir else None
            old = self.o.render_old_venv / "bin" / "python" if self.o.render_old_venv else self._build_old_venv()
            if new and new.exists() and old and Path(old).exists():
                self._render_py = (Path(old), new)
            else:
                self.render_unavailable = ("render check not run: needs an Airflow 2 venv (--render-old-venv, "
                                           "or --install-command auto to build one) and an Airflow 3 test venv")
                self.result.skipped.append(self.render_unavailable)
        return self._render_py

    def _build_old_venv(self):
        """Airflow 2 at the repo's original pin (or the ruleset's source_version), official constraints."""
        if self.o.install_command != "auto" or not self.o.venv_dir or not self.ruleset.source_version:
            return None
        original = self.originals.get(Path("requirements.txt"), "")
        m = re.search(r"^\s*apache[-_.]airflow(?:\[[^\]]*\])?==([\w.]+)", original, re.M | re.I)
        version = m.group(1) if m else str(self.ruleset.source_version)
        path = self.o.venv_dir.with_name(self.o.venv_dir.name + "-airflow2")
        py = path / "bin" / "python"
        if not py.exists():
            subprocess.run([self.o.venv_python or sys.executable, "-m", "venv", str(path)], check=False)
            constraints = self.ruleset.install_constraints.replace("{version}", version).replace(
                "{python}", f"{sys.version_info[0]}.{sys.version_info[1]}")
            req = [f"apache-airflow=={version}"]
            if original:
                tmp = Path(tempfile.mkdtemp()) / "requirements-airflow2.txt"
                tmp.write_text(original)
                req = ["-r", str(tmp)]
            self.log(f"render check: building an Airflow {version} venv")
            proc = subprocess.run([str(py), "-m", "pip", "install", "-q", *req, "-c", constraints],
                                  capture_output=True, text=True)
            if proc.returncode:
                self.log(f"render check: Airflow {version} venv failed: {proc.stderr.strip()[-300:]}")
                return None
        return py

    def _old_snapshot(self) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="aca-old-")) / "repo"
        shutil.copytree(self.repo, tmp, ignore=shutil.ignore_patterns(".git", ".venv", "venv", "__pycache__"))
        for rel, text in self.originals.items():
            (tmp / rel).write_text(text)
        return tmp

    def _render_compare(self):
        pythons = self._render_pythons()
        if not pythons:
            return None
        files = sorted(f.as_posix() for f in self.sdk_files)
        try:
            old = render_check.render(pythons[0], self._old_snapshot(), files, env=self._render_env())
            new = render_check.render(pythons[1], self.repo, files, env=self._render_env())
        except Exception as e:  # noqa: BLE001
            self.render_unavailable = f"render check failed to run: {e}"
            self.result.skipped.append(self.render_unavailable)
            self._render_py = None
            return None
        changed_files = {rel.as_posix() for rel, text in self.originals.items() if (self.repo / rel).read_text() != text}
        changed = render_check.changed_fields(old, new, changed_files)
        pinned = {r.path for rows in self.rows.values() for r in rows if self._plugin_of(r.rule_id) in WHOLE_DAG}
        # whole DAGs: those we pinned or rebased, and those left on Airflow 3's new default timetable
        whole = {k for k, d in new["dags"].items() if d["file"] in pinned or d.get("timetable") in render_check.TRIGGER}
        return render_check.compare(old, new, changed, whole), new

    def _covering(self, res, new) -> tuple[list[Row], list[Row]]:
        """(template rows, pin rows) that can affect this rendered field. A DAG left on Airflow 3's new
        timetable shifts its dates by design (see Run dates will shift): no pin or start_date change
        answers for that, only the template rewrites in its own fields."""
        span = render_check.task_spans(self.current[Path(res.file)]).get(res.task_id)
        raw = new["dags"][f"{res.file}::{res.dag_id}"]["tasks"][res.task_id]["fields"][res.field]["raw"]
        refs = {s for s in render_check._strings(raw) if s}
        templates, pins = [], []
        for rows in self.rows.values():
            for r in rows:
                plugin = self._plugin_of(r.rule_id)
                if plugin == "airflow_templates" and (
                        (r.path == res.file and span and span[0] <= r.line <= span[1])
                        or (r.path != res.file and any(r.path == s or r.path.endswith("/" + s)  # by path, or by
                                                       or (r.raw_after.strip() and r.raw_after.strip() in s)  # contents
                                                       for s in refs))):  # (DagBag inlines template files)
                    templates.append(r)
                elif plugin in WHOLE_DAG and r.path == res.file and not res.unpinned:
                    pins.append(r)
        return templates, pins

    def _revert_rows(self, rows: list[Row], reasons: dict) -> None:
        for path in sorted({r.path for r in rows}):
            rel = Path(path)
            lines = self.current[rel].splitlines(keepends=True)
            for r in sorted((r for r in rows if r.path == path), key=lambda r: -r.line):
                after, before = r.raw_after.splitlines(), r.raw_before.splitlines()
                i = r.line - 1
                if [ln.rstrip("\n") for ln in lines[i:i + len(after)]] != after:
                    self.log(f"render check: couldn't revert {path}:{r.line} exactly; left as is")
                    continue
                lines[i:i + len(after)] = [ln + "\n" for ln in before]
                self.rows[rel] = [x for x in self.rows[rel] if x is not r]
                self.render_reverted.setdefault(rel, set()).add(r.rule_id)
                self.result.manual.append(ManualItem(path, r.line, r.rule_id, reasons[id(r)]))
            new = "".join(lines)
            self.current[rel] = new
            self._write(rel, new)

    def render_gate(self) -> None:
        """Deterministic template rewrites and pins must render identically; revert any that don't."""
        compared = self._render_compare()
        if not compared:
            return
        results, new = compared
        blamed, reasons = [], {}
        for res in results:
            if not res.mismatches:
                continue
            templates, pins = self._covering(res, new)
            if not templates and not _pin_can_explain(res):
                continue  # a removed variable still waiting for judgment, not the schedule
            for r in templates or pins:
                if id(r) not in reasons:
                    blamed.append(r)
                    reasons[id(r)] = f"{render_check.summary(res)} (field {res.task_id}.{res.field})"
        if not blamed:
            return
        self.log(f"render check: reverting {len(blamed)} deterministic change(s) that render differently")
        self._revert_rows(blamed, reasons)
        if self.runner:
            self.state_outcome = self._run_tests()

    def _render_problem(self, rel: Path, original: str, proposed: str, plugin: str | None) -> str | None:
        """For a Claude patch: mismatches in the fields of the tasks it touched (None = fine / not checked)."""
        if self._render_pythons() is None:
            return None
        on_disk = (self.repo / rel).read_text()
        (self.repo / rel).write_text(proposed)
        saved = self.current[rel]
        self.current[rel] = proposed
        try:
            compared = self._render_compare()
        finally:
            self.current[rel] = saved
            (self.repo / rel).write_text(on_disk)
        if not compared:
            return None
        results, _ = compared
        spans = render_check.task_spans(proposed)
        touched = {t for t, (a, b) in spans.items()
                   if any(a <= r.line <= b for r in _rows(rel, original, proposed, "", "", ""))}
        whole = plugin in WHOLE_DAG  # a pin / start_date change answers for every field of its DAGs
        bad = [r for r in results if r.mismatches and r.file == rel.as_posix()
               and ((whole and not r.unpinned) or (not whole and r.task_id in touched))]
        if not bad:
            return None
        return ("render mismatch (old template on Airflow 2 vs yours on Airflow 3, runs paired by the moment "
                "they fire):\n" + "\n".join(render_check.mismatch_lines(bad)))

    def render_annotate(self) -> None:
        """Final pass: say on every Airflow row whether and how it was render-checked."""
        if self.ruleset.verify != "airflow-dags" or not any(self.rows.values()):
            return
        compared = self._render_compare()
        results, new = compared if compared else ([], None)
        self.result.render_results = results
        by_row: dict[int, list] = {}
        for res in results:
            templates, pins = self._covering(res, new)
            for r in templates + (pins if _pin_can_explain(res) else []):
                by_row.setdefault(id(r), []).append(res)
        unpinned_dags = {(r.file, r.dag_id) for r in results if r.unpinned}
        for rel, rows in self.rows.items():
            out = []
            for r in rows:
                plugin = self._plugin_of(r.rule_id)
                extra = None
                if plugin == "airflow_context_keys" and r.path.endswith(".py"):
                    extra = "not render-checked (Python context reads are out of scope for v0)"
                elif plugin in ("airflow_templates", *WHOLE_DAG):
                    covering = by_row.get(id(r))
                    in_unpinned = any(f == r.path for f, _ in unpinned_dags)
                    if not compared:
                        extra = f"not render-checked: {getattr(self, 'render_unavailable', 'unavailable')}"
                    elif not covering and in_unpinned:
                        extra = ("not render-checked: a DAG in this file isn't pinned, so its run dates shift "
                                 "(see Run dates will shift)")
                    elif not covering and plugin in WHOLE_DAG:
                        extra = ("not render-checked (no template field of this DAG could be compared: none, "
                                 "or only ones still using a removed variable)")
                    elif not covering:
                        extra = "not render-checked (no rendered template field uses this line)"
                    else:
                        worst = next((c for c in covering if c.mismatches), min(covering, key=lambda c: c.runs))
                        extra = render_check.summary(worst)
                        if plugin in WHOLE_DAG and not worst.mismatches:
                            extra = extra.replace("render-checked:", f"render-checked: all {len(covering)} template "
                                                  "field(s) of this DAG", 1)
                out.append(dataclasses.replace(r, note=f"{r.note}; {extra}" if r.note and extra else (extra or r.note)))
            self.rows[rel] = out

    def _revert_all(self, rules, reason, hits) -> None:
        for rel, text in self.originals.items():
            (self.repo / rel).write_text(text)
            if rel in self.current:
                self.current[rel] = text
        self.rows.clear()
        self.applied_rules = [r for r in self.applied_rules if r not in rules]
        self.pin_minimum = None
        for rule in rules:
            for rel, sites in hits[rule.id].items():
                self.result.manual += [ManualItem(rel.as_posix(), s.line, rule.id, reason) for s in sites]
        if self.runner:
            self.runner.install()

    def bump_pins(self) -> None:
        if not self.applied_rules:
            return
        minimum = max(r.min_sdk_version for r in self.applied_rules)
        if self.pin_minimum is not None and minimum <= self.pin_minimum:
            return
        # always plan from the original pin files so edits don't stack
        for rel in list(self.result.pin_plan.files if self.result.pin_plan else ()):
            (self.repo / rel).write_text(self.originals[rel])
        plan = plan_pins(self.repo, self.ruleset.dist, minimum)
        for rel in plan.files:
            self.originals.setdefault(rel, (self.repo / rel).read_text())
        apply_pins(self.repo, plan)
        self.result.pin_plan = plan
        self.pin_minimum = minimum
        if plan.files and self.runner:
            self.runner.install()

    def llm(self, hits) -> None:
        for rule in self.ruleset.rules:
            if rule.fix != "llm" or rule.id not in hits:
                continue
            if reason := self._pin_blocker(rule):
                self.result.skipped_rules.append(reason)
                continue
            why = None
            if self.model is None:
                why = "no ANTHROPIC_API_KEY, so no LLM patch was attempted"
            elif self.tests_unusable:
                why = "LLM patch not attempted: the repo's tests can't verify it (see Other notes)"
            if why:
                for rel in hits[rule.id]:
                    sites = self._sites(self.current.get(rel, (self.repo / rel).read_text()), rule, rel)
                    self.result.manual += [
                        ManualItem(rel.as_posix(), s.line, rule.id, f"{s.reason} ({why})" if s.reason else why)
                        for s in sites]
                continue
            applied = False
            for rel in hits[rule.id]:
                # re-find sites: deterministic rules may have moved lines
                sites = self._sites(self.current[rel], rule, rel)
                if sites and rel.suffix != ".py":
                    self.result.manual += [ManualItem(rel.as_posix(), s.line, rule.id,
                                                      f"{s.reason} (LLM patches are Python-only for now)")
                                           for s in sites]
                    continue
                if sites and self._llm_file(rel, rule, sites):
                    applied = True
            if applied:
                self.applied_rules.append(rule)
                self.bump_pins()

    def _validate(self, original: str, proposed: str, rule: Rule, rel: Path) -> str | None:
        if proposed == original:
            return "patch made no changes"
        try:
            module = cst.parse_module(proposed)
        except cst.ParserSyntaxError as e:
            return f"patch does not parse: {str(e).splitlines()[0]}"
        lost = missing_comments(original, proposed)
        if lost:
            listed = "; ".join(f"line {line}: `{text}`" for line, text in lost)
            return (f"patch removed comment(s) from the original: {listed}. Keep every comment "
                    "(next to the code it describes) unless you delete its whole line.")
        left = self._sites(proposed, rule, rel) if rule.match.plugin else find_sites(module, rule)
        if left:
            return f"patch still matches the rule at line(s) {', '.join(str(s.line) for s in left)}"
        return None

    def _llm_file(self, rel: Path, rule: Rule, sites: list[Site]) -> bool:
        original = self.current[rel]
        feedback: str | None = None
        reason = "no attempt made"
        for attempt in range(1, max(1, self.o.max_llm_attempts) + 1):
            self.log(f"{rel}: asking for a {rule.id} patch (attempt {attempt})")
            try:
                proposed = self.model.propose(PatchRequest(rel.as_posix(), original, rule, sites, feedback))
            except BudgetExhausted as e:
                reason = str(e)
                break
            except Exception as e:  # noqa: BLE001
                reason = f"LLM request failed: {e}"
                feedback = None
                continue
            notes = (getattr(self.model, "last_notes", "") or "").strip()
            if notes:
                self.log(f"{rel}: Claude's notes: {notes[:300]}")
            if proposed == original and notes:
                problem = f"Claude left the file unchanged: {notes}"
            else:
                problem = self._validate(original, proposed, rule, rel)
            entry = {"path": rel.as_posix(), "rule_id": rule.id, "attempt": attempt, "notes": notes}
            if problem is None and rule.match.plugin in ("airflow_templates", *WHOLE_DAG):
                problem = self._render_problem(rel, original, proposed, rule.match.plugin)
            if problem is None and self.runner:
                (self.repo / rel).write_text(proposed)
                outcome = self._run_tests(changed=[rel])
                regressions = new_failures(self.state_outcome, outcome)
                if regressions:
                    (self.repo / rel).write_text(original)
                    problem = (f"repo tests failed after the patch: {', '.join(sorted(regressions))}\n"
                               f"Test output (tail):\n{outcome.output[-3000:]}")
                else:
                    self.state_outcome = outcome
                    self.tested_after_changes = True
            if notes:
                entry["accepted"] = problem is None
                self.result.llm_notes.append(entry)
            if problem is None:
                first = re.split(r"(?<=[.!?])\s", " ".join(notes.split()), maxsplit=1)[0][:200] if notes else ""
                note = (f"Claude: {first} (full notes below)" if notes else "patched by Claude")
                self._set_source(rel, proposed, _rows(rel, original, proposed, rule.id, rule.changelog, note))
                return True
            reason = problem.splitlines()[0]
            diff = "".join(difflib.unified_diff(original.splitlines(True), proposed.splitlines(True),
                                                f"a/{rel}", f"b/{rel}"))
            feedback = f"{problem}\n\nYour patch as a diff:\n{diff[-4000:]}"
            self.log(f"{rel}: rejected: {reason}")
        self.result.manual += [ManualItem(rel.as_posix(), s.line, rule.id, reason) for s in sites]
        return False

    UNVERIFIED = ("this file fails to import on Airflow 3 before and after, so nothing in it could be verified "
                  "(see Unverified files)")

    def unverified_pass(self) -> None:
        """A file that fails on the target before *and* after: nothing in it was checked. Rebuild it from
        the original without ruff's unsafe fixes or Claude's patches, and say plainly that it's unverified."""
        before, after = self.result.tests_baseline, self.state_outcome
        if not self.runner or before is None or after is None or not after.has_junit:
            return
        bad = [rel for rel in sorted(self.rows) if rel.suffix == ".py" and self.rows[rel]
               and f"{rel.as_posix()}::import" in before.failures and f"{rel.as_posix()}::import" in after.failures]
        if not bad:
            return
        self.log(f"unverified: {len(bad)} file(s) fail on the target before and after; rebuilding without unsafe fixes")
        safe = [dataclasses.replace(t, args=tuple(a for a in t.args if a != "--unsafe-fixes")) for t in self.ruleset.tools]
        for rel in bad:
            self._rebuild_safe(rel, safe)
        self.state_outcome = self._run_tests()
        self.tested_after_changes = True
        self.render_gate()  # the rebuilt files' kept changes must render identically too
        for rel in bad:
            key = f"{rel.as_posix()}::import"
            if key not in self.state_outcome.failures:
                continue  # imports now: its checks ran like any other file's
            error = self.state_outcome.messages.get(key) or before.messages.get(key) or "import failed"
            self.result.unverified.append(Unverified(rel.as_posix(), error))
            self.rows[rel] = [dataclasses.replace(r, note=f"{r.note}; ⚠️ unverified: {self.UNVERIFIED}" if r.note
                                                  else f"⚠️ unverified: {self.UNVERIFIED}") for r in self.rows[rel]]

    def _rebuild_safe(self, rel: Path, tools) -> None:
        original = self.originals[rel]
        (self.repo / rel).write_text(original)
        self.current[rel], self.rows[rel] = original, []
        reverted = self.render_reverted.get(rel, set())
        self.result.manual = [m for m in self.result.manual if m.path != rel.as_posix() or m.rule_id in reverted]
        self.result.llm_notes = [n for n in self.result.llm_notes if n.get("path") != rel.as_posix()]
        unsafe = (lambda d: f"ruff {d.code}: unsafe fix not applied ({self.UNVERIFIED}): {d.message}"
                  if d.edit_lines else None)
        for tool in tools:
            self._record_ruff(ruff_layer.run(self.repo, [rel.as_posix()], tool), tool, unsafe)
        for rule in self.ruleset.rules:
            if "add_dependency" in rule.transform:
                continue
            if rule.fix == "deterministic" and rule in self.applied_rules and rule.id not in reverted:
                if rule.match.plugin:
                    new = PLUGINS[rule.match.plugin].apply(rel, self.current[rel], self._options(rule))
                    rows = _rows(rel, self.current[rel], new, rule.id, rule.changelog, rule.note)
                else:
                    new, edits = apply_deterministic(self.current[rel], rule)
                    rows = [Row(rel.as_posix(), e.line, e.before, e.after, rule.id, rule.changelog) for e in edits]
                if new != self.current[rel]:
                    self._set_source(rel, new, rows)
            elif rule.fix == "llm":
                self.result.manual += [
                    ManualItem(rel.as_posix(), s.line, rule.id, f"{s.reason} (no patch: {self.UNVERIFIED})"
                               if s.reason else f"no patch: {self.UNVERIFIED}")
                    for s in self._sites(self.current[rel], rule, rel)]

    def checks(self, hits) -> None:
        """`fix: manual` rules: report every site, on the final text, for a human. Never patched."""
        for rule in self.ruleset.rules:
            if rule.fix != "manual" or rule.id not in hits:
                continue
            for rel in hits[rule.id]:
                text = self.current.get(rel, (self.repo / rel).read_text())
                self.result.manual += [ManualItem(rel.as_posix(), s.line, rule.id, s.reason or rule.description)
                                       for s in self._sites(text, rule, rel)]

    def unpinned_schedules(self) -> list[dict]:
        """Schedules still on Airflow 3's new default timetables, with the render check's evidence."""
        rule = next((r for r in self.ruleset.rules if r.match.plugin == "airflow_cron_pin"), None)
        if rule is None:
            return []
        out = []
        for rel in sorted(self.current):
            if rel.suffix != ".py":
                continue
            for site in PLUGINS[rule.match.plugin].find(rel, (self.repo / rel).read_text(), self._options(rule), "llm"):
                bad = [r for r in self.result.render_results if r.file == rel.as_posix() and r.unpinned and r.mismatches]
                out.append({"path": rel.as_posix(), "line": site.line, "schedule": site.snippet, "reason": site.reason,
                            "evidence": render_check.summary(bad[0]) + f" (field {bad[0].dag_id}.{bad[0].task_id}."
                                        f"{bad[0].field})" if bad else ""})
        return out

    def dependencies(self, hits) -> None:
        """``add_dependency`` rules. Run last, so the SDK pins they look at
        already include this run's bumps (and held pins stay as they are)."""
        package = self.ruleset.dist
        for rule in self.ruleset.rules:
            if "add_dependency" not in rule.transform or rule.id not in hits:
                continue
            requirement = rule.transform["add_dependency"]
            name = Requirement(requirement).name
            importers = [f"`{rel.as_posix()}:{sites[0].line}`" for rel, sites in sorted(hits[rule.id].items())]
            shown = ", ".join(importers[:5]) + (f" and {len(importers) - 5} more" if len(importers) > 5 else "")
            plan = plan_add_dependency(self.repo, package, requirement, rule.match.sdk_allows)
            notes = self.result.rule_notes

            if plan.holds:
                h = plan.holds[0]
                self.result.skipped_rules.append(
                    f"`{rule.id}`: `{h.path}:{h.line}` says `{h.reason}`, so `{name}` was left undeclared "
                    f"even though it is imported at {shown}")
                continue
            if plan.declared:
                d = plan.declared[0]
                notes[rule.id] = f"`{name}` is already declared at `{d.path}:{d.line}`."
                continue
            if not plan.sdk:
                notes[rule.id] = (f"`{name}` is imported at {shown}, but no dependency file declares `{package}`, "
                                  "so there is no file to add it to.")
                continue
            if not plan.triggering:
                pins = ", ".join(f"`{d.path}:{d.line}` `{d.text}`" for d in plan.sdk)
                notes[rule.id] = (f"not needed yet: {pins} doesn't allow `{package}{rule.match.sdk_allows}` "
                                  f"(`{name}` is imported at {shown}).")
                continue
            for prob in plan.problems:
                self.result.manual.append(ManualItem(prob.path.as_posix(), prob.line, rule.id,
                                                     f"add `{requirement}` yourself: this file {prob.reason}"))
            if not plan.files:
                continue

            before = {rel: (self.repo / rel).read_text() for rel in plan.files}
            for rel, text in plan.files.items():
                self._write(rel, text)
            why = f"{rule.note} ({shown})" if rule.note else f"imported at {shown}"
            if self.runner:
                self.runner.install()
                outcome = self._run_tests()
                regressions = new_failures(self.state_outcome, outcome)
                if regressions:
                    for rel, text in before.items():
                        (self.repo / rel).write_text(text)
                    self.runner.install()
                    self.result.manual += [
                        ManualItem(e.path.as_posix(), e.line, rule.id,
                                   f"adding `{requirement}` broke tests: {', '.join(sorted(regressions))}")
                        for e in plan.edits]
                    continue
                self.state_outcome = outcome
                self.tested_after_changes = True
            for e in plan.edits:
                self.rows.setdefault(e.path, []).append(Row(e.path.as_posix(), e.line, "", e.text, rule.id,
                                                            rule.changelog, why))
                if e.new_line:
                    self.inserted.append((e.path, e.line))
            self.result.dep_notes += plan.notes
            self.applied_rules.append(rule)

    # -- output -------------------------------------------------------------
    def finish(self) -> RunResult:
        res = self.result
        rows = [r for rel in sorted(self.rows) for r in sorted(self.rows[rel], key=lambda r: r.line)]
        plan = res.pin_plan
        if plan and plan.edits and (rows or self.ruleset.target_version):
            if self.ruleset.target_version:  # the upgrade itself, not one rule's minimum
                rid = f"{self.ruleset.dist} {self.ruleset.target_version}"
                changelog = next((t.changelog for t in self.ruleset.tools if t.changelog), self.ruleset.rules[0].changelog)
                note = f"upgrade target; installed with {'official constraints' if self.ruleset.install_constraints else 'pip'}"
            else:
                top = max(self.applied_rules, key=lambda r: r.min_sdk_version)
                rid, changelog, note = top.id, top.changelog, ""
            for e in plan.edits:
                shift = sum(1 for path, line in self.inserted if path == e.path and line <= e.line)
                rows.append(Row(e.path.as_posix(), e.line + shift, e.before, e.after, rid, changelog, note))
            rows.sort(key=lambda r: (r.path, r.line))
        res.rows = rows
        res.changed_files = sorted(
            rel.as_posix() for rel, text in self.originals.items() if (self.repo / rel).read_text() != text
        )
        res.diff = "".join(
            "".join(difflib.unified_diff(self.originals[Path(rel)].splitlines(True),
                                         (self.repo / rel).read_text().splitlines(True), f"a/{rel}", f"b/{rel}"))
            for rel in res.changed_files)
        self._refresh_ruff_manual()
        if self.ruleset.verify == "airflow-dags":
            res.unpinned = self.unpinned_schedules()
        res.tests_after = self.state_outcome if self.tested_after_changes else None
        res.new_failures = new_failures(res.tests_baseline, res.tests_after)
        if self.model is not None and hasattr(self.model, "usage_summary"):
            res.llm_usage = self.model.usage_summary()
        slug = self.o.repo_slug
        blob = f"{self.o.server_url}/{slug}/blob/{self.o.branch}" if slug else None
        res.body = render_body(
            package=self.ruleset.dist, rows=rows, rules=self.ruleset.rules,
            pin_minimum=self.pin_minimum if plan and plan.edits else None,
            pin_conflicts=plan.conflicts if plan else [], lock_notes=plan.lock_notes if plan else [],
            rule_notes=res.rule_notes, dep_notes=res.dep_notes,
            skipped_rules=res.skipped_rules, skipped=res.skipped, manual=res.manual, unparsable=res.unparsable,
            tests_baseline=res.tests_baseline, tests_after=res.tests_after, new_failures=res.new_failures,
            blob_base=blob, rules_path=Path(self.o.rules_path).name, title=self.ruleset.title,
            tools=self.ruleset.tools if self.result.rows or rows else (),
            dag_target=(f"{self.ruleset.dist} {self.ruleset.target_version}"
                        if self.ruleset.verify == "airflow-dags" else None),
            llm_usage=res.llm_usage, llm_notes=res.llm_notes, render_results=res.render_results,
            unverified=res.unverified, unpinned=res.unpinned,
            timezone=self.tz, naive_pins=[r for r in rows if r.rule_id == "airflow3-cron-timetable-pin"
                                          and (Path(r.path), r.line) in self.naive_pin_lines()],
        )
        return res

    def open_pr(self) -> None:
        res = self.result
        default = self.o.default_branch
        if not default and self.github is not None:
            default = self.github.default_branch()
        default = default or "main"
        base = self.o.base_branch or default
        gitops.ensure_safe_branch(self.o.branch, default_branch=default, base_branch=base)
        rule_ids = sorted({r.rule_id for r in res.rows})
        what = self.ruleset.title or f"{self.ruleset.package}: migrate deprecated SDK usage"
        message = f"chore({self.ruleset.package}): {what}\n\nRules: {', '.join(rule_ids)}"
        res.pushed = gitops.publish(self.repo, branch=self.o.branch, default_branch=default, base_branch=base,
                                    files=res.changed_files, message=message)
        if self.github is None:
            return
        title = (f"{self.ruleset.title}: automated migration" if self.ruleset.title
                 else f"{self.ruleset.package} SDK: migrate deprecated API usage")
        existing = self.github.find_open_pr(self.o.branch)
        if existing:
            pr = self.github.update_pr(existing["number"], title=title, body=res.body)
        else:
            pr = self.github.create_pr(head=self.o.branch, base=base, title=title, body=res.body)
        res.pr_url = pr.get("html_url")


def run(options: Options, model: PatchModel | None = None, github=None) -> RunResult:
    r = _Run(options, model, github)
    # Fail fast on a bad branch before touching anything.
    if not options.dry_run:
        gitops.ensure_safe_branch(options.branch, default_branch=options.default_branch,
                                  base_branch=options.base_branch)
    hits = r.scan()
    if not hits:
        return r.finish()
    if r.ruleset.target_version:
        r.bump_to_target()
    r.setup_tests()
    r.deterministic(hits)
    r.llm(hits)
    r.unverified_pass()
    r.checks(hits)
    r.dependencies(hits)
    r.render_annotate()
    result = r.finish()
    if result.rows and not options.dry_run:
        r.open_pr()
    return result
