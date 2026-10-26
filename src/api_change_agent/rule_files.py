"""Custom rules: one self-contained Python file per rule, plus its test.

A rule file is a module with:

    ID = "acme-no-legacy-hook"          # unique; shown on every finding
    DESCRIPTION = "What it finds and why it matters, in a sentence or two."
    DOCS = "https://..."                 # where the reader learns more (an http(s) URL)
    FIX = "manual"                       # or "deterministic", with apply() below
    NOTE = "..."                         # optional: one line shown on each change in migrate mode's PR
    FILES = ("*.sql",)                   # optional: template or config files it also reads, by file name;
                                         # it always reads every Python file that imports airflow

    def find(path: Path, text: str) -> list[Site]: ...   # every site in one file
    def apply(path: Path, text: str) -> str: ...         # FIX = "deterministic" only: the rewritten file

`Site(line, snippet, reason)` comes from `api_change_agent.rule_files`. The action loads every `*.py` in the
repo's `.airflow-verifier/rules/` (the `custom-rules` input) except `test_*.py` and `_*.py`. Each one runs
next to the bundled rules: verify mode lists its sites, migrate mode lists or rewrites them, and a rewrite
goes through the same DAG check and render check as the bundled rules. See docs/writing-rules.md.
"""
from __future__ import annotations

import dataclasses
import fnmatch
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

from .codemod import Site
from .rules import Rule, RuleError, RuleSet, _rule

__all__ = ["Site", "load_rule_file", "with_rule_files", "DEFAULT_DIR"]

DEFAULT_DIR = ".airflow-verifier/rules"
PREFIX = "file:"  # plugin names of rule files, so they can't collide with the bundled plugins
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class FileRule:
    """A rule file, wrapped as a plugin (plugins.PLUGINS's interface)."""

    def __init__(self, module: ModuleType, path: Path):
        self.module, self.path = module, path
        self.id: str = module.ID
        self.name = PREFIX + self.id
        # non-Python files the agent also reads for this rule (templates, configs), matched by file name
        self.file_globs = tuple(getattr(module, "FILES", ()) or ())

    def handles(self, path: Path) -> bool:
        return path.suffix == ".py" or any(fnmatch.fnmatch(path.name, g) for g in self.file_globs)

    def find(self, path: Path, text: str, options: dict, mode: str) -> list[Site]:
        # deterministic rules answer in "deterministic" mode, manual ones in "llm" mode (agent._sites)
        if (mode == "deterministic") != (self.module.FIX == "deterministic") or not self.handles(Path(path)):
            return []
        return [s if isinstance(s, Site) else Site(*s) for s in self.module.find(Path(path), text)]

    def apply(self, path: Path, text: str, options: dict) -> str:
        if self.module.FIX != "deterministic" or not self.handles(Path(path)):
            return text
        return self.module.apply(Path(path), text)


def load_rule_file(path: str | Path) -> ModuleType:
    """Import one rule file and check its shape. Tests use this to load the rule they test."""
    path = Path(path).resolve()
    name = f"aca_rule_{re.sub(r'[^0-9A-Za-z_]', '_', path.stem)}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuleError(f"{path}: not a Python file")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:  # noqa: BLE001 - a broken rule file is reported, not fatal to the run
        raise RuleError(f"{path.name}: failed to import: {type(e).__name__}: {e}") from e

    def fail(msg: str) -> RuleError:
        return RuleError(f"{path.name}: {msg}")

    for attr in ("ID", "DESCRIPTION", "DOCS", "FIX", "find"):
        if not hasattr(module, attr):
            raise fail(f"needs `{attr}` (see docs/writing-rules.md)")
    if not isinstance(module.ID, str) or not _ID.match(module.ID):
        raise fail("ID must be lowercase letters, digits and dashes, e.g. acme-no-legacy-hook")
    if module.FIX not in ("manual", "deterministic"):
        raise fail('FIX must be "manual" or "deterministic"')
    if module.FIX == "deterministic" and not callable(getattr(module, "apply", None)):
        raise fail('FIX = "deterministic" needs an apply(path, text) function')
    return module


def _as_rule(module: ModuleType, plugin: FileRule) -> Rule:
    raw = {"id": module.ID, "description": module.DESCRIPTION, "changelog": module.DOCS, "fix": module.FIX,
           "match": {"plugin": plugin.name}, "min_sdk_version": "0", "note": getattr(module, "NOTE", "")}
    if module.FIX == "deterministic":
        raw["transform"] = {"plugin": plugin.name}
    return _rule(raw, 0)


def with_rule_files(ruleset: RuleSet, folder: str | Path | None, log=print) -> tuple[RuleSet, list[str]]:
    """The ruleset plus every rule file in `folder` (if it exists), and one note per file that couldn't load."""
    from .plugins import PLUGINS

    if not folder or not Path(folder).is_dir():
        return ruleset, []
    rules, notes = list(ruleset.rules), []
    taken = {r.id for r in rules} | {t.id for t in ruleset.tools}
    for path in sorted(Path(folder).glob("*.py")):
        if path.name.startswith(("test_", "_")):
            continue
        try:
            module = load_rule_file(path)
            if module.ID in taken:
                raise RuleError(f"{path.name}: ID {module.ID!r} is already used by another rule")
            plugin = FileRule(module, path)
            PLUGINS[plugin.name] = plugin
            rules.append(_as_rule(module, plugin))
        except RuleError as e:
            notes.append(f"custom rule not loaded: {e}")
            continue
        taken.add(module.ID)
        log(f"custom rule: {module.ID} ({path.name})")
    return dataclasses.replace(ruleset, rules=tuple(rules)), notes
