"""Codemods that the YAML pattern language can't express.

A plugin rule names one of these with ``match: {plugin: NAME}``. Every
plugin finds sites in one of two modes:

- ``deterministic``: sites it can rewrite exactly (``apply`` rewrites only these)
- ``llm``: sites that need judgment; each carries a ``reason`` for the PR
"""
from __future__ import annotations

import ast
import fnmatch
import re
from pathlib import Path

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

from .codemod import Site

_EMPTY = cst.Module(body=[])


# --------------------------------------------------------------------------
# Airflow 3: task-context keys looked up in Python


def _string_key(node: cst.BaseExpression) -> str | None:
    if isinstance(node, cst.SimpleString) and not node.prefix.lower().startswith(("b", "f")):
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            return None
        return value if isinstance(value, str) else None
    return None


def _subscript_key(node: cst.Subscript) -> str | None:
    if len(node.slice) != 1 or not isinstance(node.slice[0].slice, cst.Index):
        return None
    return _string_key(node.slice[0].slice.value)


def _get_call_key(node: cst.Call) -> tuple[str, str] | None:
    """`name.get("key", ...)` -> (name, key)"""
    f = node.func
    if (isinstance(f, cst.Attribute) and f.attr.value == "get" and isinstance(f.value, cst.Name)
            and node.args and node.args[0].keyword is None):
        key = _string_key(node.args[0].value)
        if key is not None:
            return f.value.value, key
    return None


class _FunctionScope:
    def __init__(self, node: cst.FunctionDef):
        p = node.params
        self.params = {x.name.value for x in (*p.posonly_params, *p.params, *p.kwonly_params)}
        star = p.star_kwarg
        self.star_kwargs = star.name.value if isinstance(star, cst.Param) else None
        self.from_current_context: set[str] = set()
        for stmt in node.body.body if isinstance(node.body, cst.IndentedBlock) else ():
            for small in getattr(stmt, "body", ()):
                if isinstance(small, cst.Assign) and isinstance(small.value, cst.Call):
                    callee = _EMPTY.code_for_node(small.value.func)
                    if callee.endswith("get_current_context"):
                        for t in small.targets:
                            if isinstance(t.target, cst.Name):
                                self.from_current_context.add(t.target.value)

    def is_context(self, name: str) -> bool:
        return name == self.star_kwargs or name in self.from_current_context or (
            name == "context" and name in self.params)


class _ContextKeys(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, options: dict, rewrite: bool):
        self.rename: dict[str, str] = options.get("rename", {})
        self.expressions: dict[str, str] = options.get("expressions", {})
        self.judgment: dict[str, str] = options.get("judgment", {})
        self.attr_rename: dict[str, dict[str, str]] = options.get("attribute_rename", {})
        self.attr_judgment: dict[str, dict[str, str]] = options.get("attribute_judgment", {})
        self.rewrite = rewrite
        self.scopes: list[_FunctionScope] = []
        self.det: list[Site] = []
        self.llm: list[Site] = []

    def _line(self, node) -> int:
        return self.get_metadata(PositionProvider, node).start.line

    def _site(self, node, bucket, reason=""):
        bucket.append(Site(self._line(node), _EMPTY.code_for_node(node), reason))

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        scope = _FunctionScope(node)
        self.scopes.append(scope)
        removed = set(self.rename) | set(self.expressions) | set(self.judgment)
        for name in sorted(scope.params & removed):
            self._site(node.name, self.llm,
                       f"`{name}` is a parameter of `{node.name.value}`: Airflow 2 passed context keys to callables "
                       f"by parameter name, Airflow 3 no longer provides `{name}`")

    def leave_FunctionDef(self, original, updated):
        self.scopes.pop()
        return updated

    def _classify(self, node, receiver: str, key: str) -> str | None:
        """'det', 'llm' or None (not a removed key)."""
        if key not in self.rename and key not in self.expressions and key not in self.judgment:
            return None
        if key in self.judgment:
            self._site(node, self.llm, self.judgment[key])
            return "llm"
        if self.scopes and self.scopes[-1].is_context(receiver):
            self._site(node, self.det)
            return "det"
        self._site(node, self.llm, f"`{receiver}[{key!r}]`: `{receiver}` may be the task context, but only the "
                                   "code that passes it in shows that; check the caller")
        return "llm"

    def _replacement(self, receiver: str, key: str):
        """(expression to splice in, None) or (None, renamed key)."""
        if key in self.rename:
            return None, self.rename[key]
        return cst.parse_expression(self.expressions[key].format(ctx=receiver)), None

    def leave_Subscript(self, original: cst.Subscript, updated: cst.Subscript):
        key = _subscript_key(original)
        if key is None or not isinstance(original.value, cst.Name):
            return updated
        if self._classify(original, original.value.value, key) != "det" or not self.rewrite:
            return updated
        expr, new_key = self._replacement(original.value.value, key)
        if expr is not None:
            return expr
        old = original.slice[0].slice.value
        new_str = old.with_changes(value=old.prefix + old.quote + new_key + old.quote)
        return updated.with_changes(slice=[updated.slice[0].with_changes(slice=cst.Index(value=new_str))])

    def leave_Call(self, original: cst.Call, updated: cst.Call):
        hit = _get_call_key(original)
        if hit is None:
            return updated
        receiver, key = hit
        if self._classify(original, receiver, key) != "det" or not self.rewrite:
            return updated
        expr, new_key = self._replacement(receiver, key)
        if expr is not None:
            return expr
        old = original.args[0].value
        new_str = old.with_changes(value=old.prefix + old.quote + new_key + old.quote)
        return updated.with_changes(args=[updated.args[0].with_changes(value=new_str), *updated.args[1:]])

    def leave_Attribute(self, original: cst.Attribute, updated: cst.Attribute):
        if not isinstance(original.value, cst.Name):
            return updated
        owner, attr = original.value.value, original.attr.value
        if attr in self.attr_judgment.get(owner, {}):
            self._site(original, self.llm, self.attr_judgment[owner][attr])
        elif attr in self.attr_rename.get(owner, {}):
            self._site(original, self.det)
            if self.rewrite:
                return updated.with_changes(attr=cst.Name(self.attr_rename[owner][attr]))
        return updated


class AirflowContextKeys:
    """`context["execution_date"]` and friends in task code (Python files)."""

    name = "airflow_context_keys"
    file_globs: tuple[str, ...] = ()

    def _run(self, text: str, options: dict, rewrite: bool):
        module = cst.parse_module(text)
        t = _ContextKeys(options, rewrite)
        new = MetadataWrapper(module).visit(t)
        return new.code, t

    def find(self, path: Path, text: str, options: dict, mode: str) -> list[Site]:
        if path.suffix != ".py":
            return []
        _, t = self._run(text, options, rewrite=False)
        return t.det if mode == "deterministic" else t.llm

    def apply(self, path: Path, text: str, options: dict) -> str:
        if path.suffix != ".py":
            return text
        new, _ = self._run(text, options, rewrite=True)
        return new


# --------------------------------------------------------------------------
# Airflow 3: removed keys inside Jinja templates

_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
_STRING_IN_TEMPLATE = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")


def _outside_strings(block: str):
    """Yield (start, end) spans of `block` that are not inside quoted strings."""
    pos = 0
    for m in _STRING_IN_TEMPLATE.finditer(block):
        yield pos, m.start()
        pos = m.end()
    yield pos, len(block)


class TemplateRewriter:
    def __init__(self, options: dict):
        self.rename: dict[str, str] = options.get("rename", {})
        self.expressions: dict[str, str] = options.get("template_expressions", {})
        self.judgment: dict[str, str] = options.get("judgment", {})
        self.attr_rename: dict[str, dict[str, str]] = options.get("attribute_rename", {})
        self.attr_judgment: dict[str, dict[str, str]] = options.get("attribute_judgment", {})
        names = sorted(set(self.rename) | set(self.expressions) | set(self.judgment), key=len, reverse=True)
        self.bare = re.compile(r"(?<![\w.])(" + "|".join(map(re.escape, names)) + r")(?![\w])") if names else None
        owners = sorted(set(self.attr_rename) | set(self.attr_judgment))
        self.attr = re.compile(r"(?<![\w.])(" + "|".join(map(re.escape, owners)) + r")\s*\.\s*(\w+)") if owners else None

    def rewrite(self, text: str, forbidden_quote: str | None = None):
        """Returns (new_text, deterministic_hits, llm_hits); hits are (offset, snippet, reason)."""
        det, llm, out, last = [], [], [], 0
        for block in _JINJA.finditer(text):
            new_block = self._block(block.group(0), block.start(), forbidden_quote, det, llm)
            out.append(text[last:block.start()])
            out.append(new_block)
            last = block.end()
        out.append(text[last:])
        return "".join(out), det, llm

    def _block(self, block, base, forbidden_quote, det, llm):
        pieces, last = [], 0
        for start, end in _outside_strings(block):
            pieces.append(block[last:start])
            pieces.append(self._code(block[start:end], base + start, forbidden_quote, det, llm))
            last = end
        pieces.append(block[last:])
        return "".join(pieces)

    def _code(self, code, base, forbidden_quote, det, llm):
        def attr_sub(m):
            owner, attr = m.group(1), m.group(2)
            if attr in self.attr_judgment.get(owner, {}):
                llm.append((base + m.start(), m.group(0), self.attr_judgment[owner][attr]))
            elif attr in self.attr_rename.get(owner, {}):
                det.append((base + m.start(), m.group(0), ""))
                return f"{owner}.{self.attr_rename[owner][attr]}"
            return m.group(0)

        def bare_sub(m):
            key = m.group(1)
            if key in self.judgment:
                llm.append((base + m.start(), key, self.judgment[key]))
                return key
            if key in self.rename:
                det.append((base + m.start(), key, ""))
                return self.rename[key]
            expr = self.expressions[key]
            if forbidden_quote and forbidden_quote in expr:
                other = "'" if forbidden_quote == '"' else '"'
                if other in expr:
                    llm.append((base + m.start(), key, "the replacement needs both quote characters here"))
                    return key
                expr = expr.replace(forbidden_quote, other)
            det.append((base + m.start(), key, ""))
            return expr

        if self.attr:
            code = self.attr.sub(attr_sub, code)
        if self.bare:
            code = self.bare.sub(bare_sub, code)
        return code


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


class _LiteralTemplates(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, rw: TemplateRewriter, rewrite: bool):
        self.rw, self.rewrite = rw, rewrite
        self.det: list[Site] = []
        self.llm: list[Site] = []

    def leave_SimpleString(self, original: cst.SimpleString, updated: cst.SimpleString):
        prefix = original.prefix.lower()
        if "b" in prefix or "f" in prefix or ("{{" not in original.value and "{%" not in original.value):
            return updated
        quote = original.quote
        inner = original.value[len(original.prefix) + len(quote): len(original.value) - len(quote)]
        new_inner, det, llm = self.rw.rewrite(inner, forbidden_quote=None if len(quote) == 3 else quote)
        line0 = self.get_metadata(PositionProvider, original).start.line
        for bucket, hits in ((self.det, det), (self.llm, llm)):
            for off, snippet, reason in hits:
                bucket.append(Site(line0 + inner.count("\n", 0, off), snippet, reason))
        if self.rewrite and new_inner != inner:
            return updated.with_changes(value=original.prefix + quote + new_inner + quote)
        return updated


class AirflowTemplates:
    """Removed context keys in Jinja: operator string args in DAG files, and template files."""

    name = "airflow_templates"
    file_globs = ("*.sql", "*.sh", "*.bash", "*.hql", "*.j2", "*.jinja", "*.jinja2", "*.tpl")

    def _python(self, text, options, rewrite):
        t = _LiteralTemplates(TemplateRewriter(options), rewrite)
        new = MetadataWrapper(cst.parse_module(text)).visit(t)
        return new.code, t.det, t.llm

    def _file(self, text, options):
        new, det, llm = TemplateRewriter(options).rewrite(text)
        to_sites = lambda hits: [Site(_line_of(text, off), snip, reason) for off, snip, reason in hits]
        return new, to_sites(det), to_sites(llm)

    def handles(self, path: Path) -> bool:
        return path.suffix == ".py" or any(fnmatch.fnmatch(path.name, g) for g in self.file_globs)

    def find(self, path: Path, text: str, options: dict, mode: str) -> list[Site]:
        if not self.handles(path):
            return []
        _, det, llm = self._python(text, options, False) if path.suffix == ".py" else self._file(text, options)
        return det if mode == "deterministic" else llm

    def apply(self, path: Path, text: str, options: dict) -> str:
        if not self.handles(path):
            return text
        return self._python(text, options, True)[0] if path.suffix == ".py" else self._file(text, options)[0]


PLUGINS = {p.name: p for p in (AirflowContextKeys(), AirflowTemplates())}
