"""Dotted attribute-chain patterns.

``*.beta.chat.completions.{parse,stream}``: a leading ``*`` matches any
receiver (one or more chain elements, including calls and subscripts), a
``*`` elsewhere matches exactly one name, and ``{a,b}`` is alternation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import libcst as cst

ANY = None  # a segment that matches any single name

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ChainPattern:
    source: str
    open_prefix: bool
    segments: tuple[frozenset[str] | None, ...]

    @classmethod
    def parse(cls, text: str) -> ChainPattern:
        parts = text.strip().split(".")
        open_prefix = parts[0] == "*" and len(parts) > 1
        if open_prefix:
            parts = parts[1:]
        segments: list[frozenset[str] | None] = []
        for part in parts:
            if part == "*":
                segments.append(ANY)
                continue
            names = part[1:-1].split(",") if part.startswith("{") and part.endswith("}") else [part]
            names = [n.strip() for n in names]
            if not names or not all(_NAME.match(n) for n in names):
                raise ValueError(f"bad segment {part!r} in pattern {text!r}")
            segments.append(frozenset(names))
        return cls(text, open_prefix, tuple(segments))

    def matches(self, chain: list[str | None] | None) -> bool:
        """``chain`` is the output of :func:`chain_of`."""
        if not chain:
            return False
        n = len(self.segments)
        if self.open_prefix:
            if len(chain) <= n:
                return False
            tail = chain[-n:]
        else:
            if len(chain) != n:
                return False
            tail = chain
        return all(seg is ANY or name in seg for seg, name in zip(self.segments, tail))

    def index_from_end(self, name: str) -> int:
        """How many ``.value`` hops from the matched node to the segment ``name``
        is (1 = the outermost attribute itself)."""
        for i, seg in enumerate(self.segments):
            if seg == frozenset([name]):
                return len(self.segments) - i
        raise ValueError(f"{name!r} is not a literal segment of {self.source!r}")


def chain_of(node: cst.BaseExpression) -> list[str | None] | None:
    """``a.b().c.d`` -> ``[None, 'c', 'd']``; ``a.b.c`` -> ``['a', 'b', 'c']``.

    Any non-name receiver collapses into a single ``None`` element. Returns
    None for nodes that aren't attribute chains.
    """
    if not isinstance(node, cst.Attribute):
        return None
    names: list[str | None] = []
    cur: cst.BaseExpression = node
    while isinstance(cur, cst.Attribute):
        names.append(cur.attr.value)
        cur = cur.value
    names.append(cur.value if isinstance(cur, cst.Name) else None)
    names.reverse()
    return names
