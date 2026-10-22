"""libcst-based site finding and deterministic rewrites."""
from __future__ import annotations

import re
from dataclasses import dataclass

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

from .patterns import chain_of
from .rules import Rule


@dataclass(frozen=True)
class Edit:
    line: int
    before: str
    after: str


@dataclass(frozen=True)
class Site:
    line: int
    snippet: str


_EMPTY = cst.Module(body=[])


def compact(code: str) -> str:
    """One-line form of an expression for the report."""
    code = " ".join(code.split())
    return re.sub(r"\s*\.\s*", ".", code)


def _call_matches(node: cst.Call, rule: Rule) -> bool:
    if not rule.match.call.matches(chain_of(node.func)):
        return False
    if not rule.match.kwargs:
        return True
    return any(a.keyword is not None and a.keyword.value in rule.match.kwargs for a in node.args)


class _SiteFinder(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, rule: Rule):
        self.rule = rule
        self.sites: list[Site] = []

    def _add(self, node: cst.CSTNode) -> None:
        line = self.get_metadata(PositionProvider, node).start.line
        self.sites.append(Site(line, _EMPTY.code_for_node(node)))

    def visit_Attribute(self, node: cst.Attribute) -> None:
        if self.rule.match.attribute and self.rule.match.attribute.matches(chain_of(node)):
            self._add(node)

    def visit_Call(self, node: cst.Call) -> None:
        if self.rule.match.call and _call_matches(node, self.rule):
            self._add(node)


def find_sites(source: str | cst.Module, rule: Rule) -> list[Site]:
    module = cst.parse_module(source) if isinstance(source, str) else source
    finder = _SiteFinder(rule)
    MetadataWrapper(module).visit(finder)
    return finder.sites


def _drop_at(node: cst.BaseExpression, hops: int, name: str) -> cst.BaseExpression:
    """Remove the attribute ``name`` that sits ``hops`` levels down ``node``."""
    if hops == 1:
        assert isinstance(node, cst.Attribute) and node.attr.value == name
        return node.value
    assert isinstance(node, cst.Attribute)
    return node.with_changes(value=_drop_at(node.value, hops - 1, name))


class _DropAttribute(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, rule: Rule):
        self.rule = rule
        self.name = rule.transform["drop_attribute"]
        self.pattern = rule.match.pattern
        self.hops = self.pattern.index_from_end(self.name)
        self.edits: list[Edit] = []

    def _target(self, original: cst.CSTNode) -> cst.BaseExpression | None:
        if self.rule.match.attribute:
            return original if self.pattern.matches(chain_of(original)) else None
        if isinstance(original, cst.Call) and _call_matches(original, self.rule):
            return original.func
        return None

    def leave_Attribute(self, original: cst.Attribute, updated: cst.Attribute):
        if self.rule.match.attribute and self._target(original) is not None:
            new = _drop_at(updated, self.hops, self.name)
            self._record(original, original, new)
            return new
        return updated

    def leave_Call(self, original: cst.Call, updated: cst.Call):
        if self.rule.match.call and self._target(original) is not None:
            new_func = _drop_at(updated.func, self.hops, self.name)
            self._record(original, original.func, new_func)
            return updated.with_changes(func=new_func)
        return updated

    def _record(self, anchor, before, after):
        line = self.get_metadata(PositionProvider, anchor).start.line
        self.edits.append(
            Edit(line, compact(_EMPTY.code_for_node(before)), compact(_EMPTY.code_for_node(after)))
        )


def apply_deterministic(source: str, rule: Rule) -> tuple[str, list[Edit]]:
    if rule.fix != "deterministic":
        raise ValueError(f"rule {rule.id} is not deterministic")
    module = cst.parse_module(source)
    transformer = _DropAttribute(rule)
    new_module = MetadataWrapper(module).visit(transformer)
    edits = sorted(transformer.edits, key=lambda e: e.line)
    return (new_module.code if edits else source), edits
