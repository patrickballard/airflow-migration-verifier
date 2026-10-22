"""Load and validate the YAML rules file."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from packaging.version import InvalidVersion, Version

from .patterns import ChainPattern

FIX_TYPES = ("deterministic", "llm")
TRANSFORMS = ("drop_attribute",)


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Match:
    attribute: ChainPattern | None = None
    call: ChainPattern | None = None
    kwargs: tuple[str, ...] = ()

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
    if (attribute is None) == (call is None):
        raise fail("match needs exactly one of 'attribute' or 'call'")
    kwargs = tuple(m.get("kwargs") or ())
    if kwargs and call is None:
        raise fail("match.kwargs only applies to 'call' patterns")

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
        if attribute is None and call is None:
            raise fail("transform needs a pattern")
        try:
            (attribute or call).index_from_end(transform["drop_attribute"])
        except ValueError as e:
            raise fail(f"transform: {e}") from e

    return Rule(
        id=rid,
        match=Match(attribute=attribute, call=call, kwargs=kwargs),
        fix=fix,
        min_sdk_version=min_version,
        changelog=changelog,
        description=" ".join(str(raw.get("description") or "").split()),
        transform=transform,
        guidance=str(raw.get("guidance") or ""),
    )
