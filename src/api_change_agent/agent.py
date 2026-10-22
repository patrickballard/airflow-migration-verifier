"""Orchestration: scan -> deterministic fixes -> pins -> LLM fixes -> tests -> PR."""
from __future__ import annotations

import difflib
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import libcst as cst
from packaging.version import Version

from . import gitops
from .codemod import Site, apply_deterministic, find_sites
from .llm import PatchModel, PatchRequest
from .pins import PinPlan, apply_pins, plan_pins
from .report import Row, render_body
from .rules import Rule, load_rules
from .scanner import scan_repo
from .testrun import TestOutcome, TestRunner, default_install_command, detect_test_command, new_failures

DEFAULT_BRANCH = "api-change-agent/openai"


@dataclass
class Options:
    repo: Path
    rules_path: Path
    dry_run: bool = False
    test_command: str | None = "auto"  # None or "" disables tests
    install_command: str | None = None  # "auto" builds one from the repo's files
    venv_dir: Path | None = None
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


@dataclass
class RunResult:
    rows: list[Row] = field(default_factory=list)
    manual: list[ManualItem] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    unparsable: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    pin_plan: PinPlan | None = None
    tests_baseline: TestOutcome | None = None
    tests_after: TestOutcome | None = None
    new_failures: set[str] = field(default_factory=set)
    pushed: bool = False
    pr_url: str | None = None
    body: str = ""


# --------------------------------------------------------------------------
# line bookkeeping


def _hunks(old: str, new: str):
    a, b = old.splitlines(), new.splitlines()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            yield max(j1, 0) + 1, textwrap.dedent("\n".join(a[i1:i2])), textwrap.dedent("\n".join(b[j1:j2]))


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

    # -- helpers ------------------------------------------------------------
    def _write(self, rel: Path, text: str) -> None:
        self.originals.setdefault(rel, (self.repo / rel).read_text())
        (self.repo / rel).write_text(text)

    def _set_source(self, rel: Path, new: str, new_rows: list[Row]) -> None:
        remap = _line_map(self.current[rel], new)
        self.rows[rel] = [
            Row(r.path, remap(r.line), r.before, r.after, r.rule_id, r.changelog) for r in self.rows.get(rel, [])
        ] + new_rows
        self.current[rel] = new
        self._write(rel, new)

    def _pin_conflicts(self, rule: Rule) -> str | None:
        conflicts = plan_pins(self.repo, self.ruleset.package, rule.min_sdk_version).conflicts
        if not conflicts:
            return None
        where = ", ".join(f"`{c.path}:{c.line}` `{c.text}`" for c in conflicts)
        return f"rule `{rule.id}` skipped: {where} can't be raised to >={rule.min_sdk_version} ({conflicts[0].reason})"

    def _run_tests(self) -> TestOutcome | None:
        return self.runner.run() if self.runner else None

    # -- stages ---------------------------------------------------------------
    def scan(self) -> dict[str, dict[Path, list[Site]]]:
        scan = scan_repo(self.repo, self.ruleset.package, self.o.exclude)
        self.result.unparsable = [p.as_posix() for p in scan.unparsable]
        hits: dict[str, dict[Path, list[Site]]] = {}
        for rel in scan.files:
            self.current[rel] = (self.repo / rel).read_text()
            for rule in self.ruleset.rules:
                sites = find_sites(self.current[rel], rule)
                if sites:
                    hits.setdefault(rule.id, {})[rel] = sites
                    self.originals.setdefault(rel, self.current[rel])
        self.log(f"scanned {len(scan.files)} file(s) importing {self.ruleset.package}; "
                 + ", ".join(f"{rid}: {sum(map(len, f.values()))} site(s)" for rid, f in hits.items()) or "no hits")
        return hits

    def setup_tests(self) -> None:
        command = self.o.test_command
        if command == "auto":
            command = detect_test_command(self.repo)
        if not command:
            return
        install = self.o.install_command
        if install == "auto":
            install = default_install_command(self.repo, command)
        self.runner = TestRunner(self.repo, command, install, self.o.venv_dir, log=self.log)
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

    def deterministic(self, hits) -> None:
        stage_rules = []
        for rule in self.ruleset.rules:
            if rule.fix != "deterministic" or rule.id not in hits:
                continue
            if reason := self._pin_conflicts(rule):
                self.result.skipped.append(reason)
                continue
            for rel in hits[rule.id]:
                new, edits = apply_deterministic(self.current[rel], rule)
                rows = [Row(rel.as_posix(), e.line, e.before, e.after, rule.id, rule.changelog) for e in edits]
                self._set_source(rel, new, rows)
            stage_rules.append(rule)
        if not stage_rules:
            return
        self.applied_rules += stage_rules
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
        plan = plan_pins(self.repo, self.ruleset.package, minimum)
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
            if reason := self._pin_conflicts(rule):
                self.result.skipped.append(reason)
                continue
            why = None
            if self.model is None:
                why = "no ANTHROPIC_API_KEY, so no LLM patch was attempted"
            elif self.tests_unusable:
                why = "LLM patch not attempted: the repo's tests can't verify it (see Skipped)"
            if why:
                for rel, sites in hits[rule.id].items():
                    self.result.manual += [ManualItem(rel.as_posix(), s.line, rule.id, why) for s in sites]
                continue
            applied = False
            for rel in hits[rule.id]:
                # re-find sites: deterministic rules may have moved lines
                sites = find_sites(self.current[rel], rule)
                if sites and self._llm_file(rel, rule, sites):
                    applied = True
            if applied:
                self.applied_rules.append(rule)
                self.bump_pins()

    def _validate(self, original: str, proposed: str, rule: Rule) -> str | None:
        if proposed == original:
            return "patch made no changes"
        try:
            module = cst.parse_module(proposed)
        except cst.ParserSyntaxError as e:
            return f"patch does not parse: {str(e).splitlines()[0]}"
        left = find_sites(module, rule)
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
            except Exception as e:  # noqa: BLE001
                reason = f"LLM request failed: {e}"
                feedback = None
                continue
            problem = self._validate(original, proposed, rule)
            if problem is None and self.runner:
                (self.repo / rel).write_text(proposed)
                outcome = self._run_tests()
                regressions = new_failures(self.state_outcome, outcome)
                if regressions:
                    (self.repo / rel).write_text(original)
                    problem = (f"repo tests failed after the patch: {', '.join(sorted(regressions))}\n"
                               f"Test output (tail):\n{outcome.output[-3000:]}")
                else:
                    self.state_outcome = outcome
                    self.tested_after_changes = True
            if problem is None:
                rows = [Row(rel.as_posix(), line, before, after, rule.id, rule.changelog)
                        for line, before, after in _hunks(original, proposed)]
                self._set_source(rel, proposed, rows)
                return True
            reason = problem.splitlines()[0]
            diff = "".join(difflib.unified_diff(original.splitlines(True), proposed.splitlines(True),
                                                f"a/{rel}", f"b/{rel}"))
            feedback = f"{problem}\n\nYour patch as a diff:\n{diff[-4000:]}"
            self.log(f"{rel}: rejected: {reason}")
        self.result.manual += [ManualItem(rel.as_posix(), s.line, rule.id, reason) for s in sites]
        return False

    # -- output -------------------------------------------------------------
    def finish(self) -> RunResult:
        res = self.result
        rows = [r for rel in sorted(self.rows) for r in sorted(self.rows[rel], key=lambda r: r.line)]
        plan = res.pin_plan
        if plan and plan.edits and rows:
            top = max(self.applied_rules, key=lambda r: r.min_sdk_version)
            rows += [Row(e.path.as_posix(), e.line, e.before, e.after, top.id, top.changelog) for e in plan.edits]
        res.rows = rows
        res.changed_files = sorted(
            rel.as_posix() for rel, text in self.originals.items() if (self.repo / rel).read_text() != text
        )
        res.tests_after = self.state_outcome if self.tested_after_changes else None
        res.new_failures = new_failures(res.tests_baseline, res.tests_after)
        slug = self.o.repo_slug
        blob = f"{self.o.server_url}/{slug}/blob/{self.o.branch}" if slug else None
        res.body = render_body(
            package=self.ruleset.package, rows=rows, rules=self.ruleset.rules,
            pin_minimum=self.pin_minimum if plan and plan.edits else None,
            pin_conflicts=plan.conflicts if plan else [], lock_notes=plan.lock_notes if plan else [],
            skipped=res.skipped, manual=res.manual, unparsable=res.unparsable,
            tests_baseline=res.tests_baseline, tests_after=res.tests_after, new_failures=res.new_failures,
            blob_base=blob, rules_path=Path(self.o.rules_path).name,
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
        message = f"chore({self.ruleset.package}): migrate deprecated SDK usage\n\nRules: {', '.join(rule_ids)}"
        res.pushed = gitops.publish(self.repo, branch=self.o.branch, default_branch=default, base_branch=base,
                                    files=res.changed_files, message=message)
        if self.github is None:
            return
        title = f"{self.ruleset.package} SDK: migrate deprecated API usage"
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
    r.setup_tests()
    r.deterministic(hits)
    r.llm(hits)
    result = r.finish()
    if result.rows and not options.dry_run:
        r.open_pr()
    return result
