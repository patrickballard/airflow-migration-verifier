"""Orchestration: scan -> deterministic fixes -> pins -> LLM fixes -> tests -> PR."""
from __future__ import annotations

import dataclasses
import difflib
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import libcst as cst
from packaging.requirements import Requirement
from packaging.version import Version

from . import gitops
from .codemod import Site, apply_deterministic, find_sites, missing_comments
from .deps import plan_add_dependency
from .llm import PatchModel, PatchRequest
from .pins import PinPlan, apply_pins, plan_pins
from .report import Row, render_body
from .rules import Rule, load_rules
from .scanner import iter_python_files, scan_repo
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
    venv_python: str | None = None  # interpreter for the test venv (default: this one)
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
    skipped_rules: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # other notes (tests, ...)
    unparsable: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    pin_plan: PinPlan | None = None
    tests_baseline: TestOutcome | None = None
    tests_after: TestOutcome | None = None
    new_failures: set[str] = field(default_factory=set)
    rule_notes: dict[str, str] = field(default_factory=dict)  # rule id -> why it did/didn't apply
    dep_notes: list[str] = field(default_factory=list)
    pushed: bool = False
    pr_url: str | None = None
    body: str = ""
    diff: str = ""  # unified diff of everything the run changed

    def summary(self) -> dict:
        """JSON-friendly record of the run (what --output writes as result.json)."""
        def tests(t):
            return None if t is None else {"command": t.command, "passed": t.passed, "returncode": t.returncode,
                                           "failures": sorted(t.failures), "summary": t.summary()}
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
        }


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
        self.inserted: list[tuple[Path, int]] = []  # (file, line) of whole lines added by dependency rules

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
        plan = plan_pins(self.repo, self.ruleset.package, rule.min_sdk_version)
        if plan.holds:
            where = ", ".join(f"`{h.path}:{h.line}` `{h.text}` (`{h.comment}`)" for h in plan.holds)
            return (f"`{rule.id}`: needs `{self.ruleset.package}>={rule.min_sdk_version}`, but {where} "
                    "is marked as intentional, so the pin was left alone and the rule was not applied")
        if plan.conflicts:
            where = ", ".join(f"`{c.path}:{c.line}` `{c.text}`" for c in plan.conflicts)
            return (f"`{rule.id}`: {where} can't be raised to >={rule.min_sdk_version} "
                    f"({plan.conflicts[0].reason})")
        return None

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
                if rule.match.module:
                    continue
                sites = find_sites(self.current[rel], rule)
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
        found = ", ".join(f"{rid}: {sum(map(len, f.values()))} site(s)" for rid, f in hits.items())
        self.log(f"scanned {len(scan.files)} file(s) importing {self.ruleset.package}; {found or 'no hits'}")
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

    def deterministic(self, hits) -> None:
        stage_rules = []
        for rule in self.ruleset.rules:
            if rule.fix != "deterministic" or rule.id not in hits or "add_dependency" in rule.transform:
                continue
            if reason := self._pin_blocker(rule):
                self.result.skipped_rules.append(reason)
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
            if reason := self._pin_blocker(rule):
                self.result.skipped_rules.append(reason)
                continue
            why = None
            if self.model is None:
                why = "no ANTHROPIC_API_KEY, so no LLM patch was attempted"
            elif self.tests_unusable:
                why = "LLM patch not attempted: the repo's tests can't verify it (see Other notes)"
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
        lost = missing_comments(original, proposed)
        if lost:
            listed = "; ".join(f"line {line}: `{text}`" for line, text in lost)
            return (f"patch removed comment(s) from the original: {listed}. Keep every comment "
                    "(next to the code it describes) unless you delete its whole line.")
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

    def dependencies(self, hits) -> None:
        """``add_dependency`` rules. Run last, so the SDK pins they look at
        already include this run's bumps (and held pins stay as they are)."""
        package = self.ruleset.package
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
        if plan and plan.edits and rows:
            top = max(self.applied_rules, key=lambda r: r.min_sdk_version)
            for e in plan.edits:
                shift = sum(1 for path, line in self.inserted if path == e.path and line <= e.line)
                rows.append(Row(e.path.as_posix(), e.line + shift, e.before, e.after, top.id, top.changelog))
            rows.sort(key=lambda r: (r.path, r.line))
        res.rows = rows
        res.changed_files = sorted(
            rel.as_posix() for rel, text in self.originals.items() if (self.repo / rel).read_text() != text
        )
        res.diff = "".join(
            "".join(difflib.unified_diff(self.originals[Path(rel)].splitlines(True),
                                         (self.repo / rel).read_text().splitlines(True), f"a/{rel}", f"b/{rel}"))
            for rel in res.changed_files)
        res.tests_after = self.state_outcome if self.tested_after_changes else None
        res.new_failures = new_failures(res.tests_baseline, res.tests_after)
        slug = self.o.repo_slug
        blob = f"{self.o.server_url}/{slug}/blob/{self.o.branch}" if slug else None
        res.body = render_body(
            package=self.ruleset.package, rows=rows, rules=self.ruleset.rules,
            pin_minimum=self.pin_minimum if plan and plan.edits else None,
            pin_conflicts=plan.conflicts if plan else [], lock_notes=plan.lock_notes if plan else [],
            rule_notes=res.rule_notes, dep_notes=res.dep_notes,
            skipped_rules=res.skipped_rules, skipped=res.skipped, manual=res.manual, unparsable=res.unparsable,
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
    r.dependencies(hits)
    result = r.finish()
    if result.rows and not options.dry_run:
        r.open_pr()
    return result
