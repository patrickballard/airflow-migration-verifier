"""Load and validate the YAML rules file."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from .patterns import ChainPattern

FIX_TYPES = ("deterministic", "llm")
TRANSFORMS = ("drop_attribute", "add_dependency")
_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Match:
    attribute: ChainPattern | None = None
    call: ChainPattern | None = None
    kwargs: tuple[str, ...] = ()
    # ``import: httpx`` matches imports of a module in *any* .py file, not
    # only the ones importing the SDK.
    module: str | None = None
    # Only trigger when the repo's SDK pin (after this run's bumps) allows
    # some version in this range.
    sdk_allows: SpecifierSet | None = None

    @property
    def pattern(self) -> ChainPattern:
        return self.attribute or self.call  # type: ignore[return-value]


@dataclass(frozen=True)
class Rule:
    id: str
    match: Match
    fix: Literal["deterministic", "llm"]
    min_sdk_version: Version
    changelog: str
    description: str = ""
    transform: dict = field(default_factory=dict)
    guidance: str = ""
    note: str = ""  # one-line "why" shown on each PR row

    @property
    def needs_sdk_bump(self) -> bool:
        return self.min_sdk_version > Version("0")


@dataclass(frozen=True)
class RuleSet:
    package: str
    rules: tuple[Rule, ...]


def load_rules(path: str | Path) -> RuleSet:
    data = yaml.safe_load(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise RuleError(f"{path}: expected a mapping with a 'rules' list")
    package = data.get("package", "openai")
    rules = tuple(_rule(r, i) for i, r in enumerate(data["rules"]))
    ids = [r.id for r in rules]
    if len(set(ids)) != len(ids):
        raise RuleError(f"{path}: duplicate rule ids")
    return RuleSet(package=package, rules=rules)


def _rule(raw: dict, index: int) -> Rule:
    rid = raw.get("id") or f"rule-{index}"

    def fail(msg: str) -> RuleError:
        return RuleError(f"rule {rid!r}: {msg}")

    fix = raw.get("fix")
    if fix not in FIX_TYPES:
        raise fail(f"fix must be one of {FIX_TYPES}, got {fix!r}")

    m = raw.get("match") or {}
    try:
        attribute = ChainPattern.parse(m["attribute"]) if m.get("attribute") else None
        call = ChainPattern.parse(m["call"]) if m.get("call") else None
    except ValueError as e:
        raise fail(f"match: {e}") from e
    module = m.get("import")
    if module is not None and not _MODULE.match(str(module)):
        raise fail(f"match.import must be a module name, got {module!r}")
    if sum(x is not None for x in (attribute, call, module)) != 1:
        raise fail("match needs exactly one of 'attribute', 'call' or 'import'")
    kwargs = tuple(m.get("kwargs") or ())
    if kwargs and call is None:
        raise fail("match.kwargs only applies to 'call' patterns")
    sdk_allows = None
    if m.get("sdk_allows"):
        try:
            sdk_allows = SpecifierSet(str(m["sdk_allows"]))
        except InvalidSpecifier as e:
            raise fail(f"match.sdk_allows: {e}") from e

    try:
        min_version = Version(str(raw.get("min_sdk_version")))
    except InvalidVersion as e:
        raise fail(f"min_sdk_version: {e}") from e

    changelog = str(raw.get("changelog") or "")
    if not changelog.startswith(("https://", "http://")):
        raise fail("changelog must be an http(s) URL")

    transform = dict(raw.get("transform") or {})
    if fix == "deterministic":
        if len(transform) != 1 or next(iter(transform)) not in TRANSFORMS:
            raise fail(f"deterministic rules need a transform, one of {TRANSFORMS}")
        if "drop_attribute" in transform:
            if attribute is None and call is None:
                raise fail("drop_attribute needs an 'attribute' or 'call' pattern")
            try:
                (attribute or call).index_from_end(transform["drop_attribute"])
            except ValueError as e:
                raise fail(f"transform: {e}") from e
        else:
            if module is None:
                raise fail("add_dependency needs an 'import' match")
            try:
                Requirement(str(transform["add_dependency"]))
            except InvalidRequirement as e:
                raise fail(f"transform.add_dependency: {e}") from e

    return Rule(
        id=rid,
        match=Match(attribute=attribute, call=call, kwargs=kwargs, module=module, sdk_allows=sdk_allows),
        fix=fix,
        min_sdk_version=min_version,
        changelog=changelog,
        description=" ".join(str(raw.get("description") or "").split()),
        transform=transform,
        guidance=str(raw.get("guidance") or ""),
        note=" ".join(str(raw.get("note") or "").split()),
    )
