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
            if not isinstance(stmt, cst.SimpleStatementLine):
                continue  # if/for/with/...: their body is a block, not statements
            for small in stmt.body:
                if isinstance(small, cst.Assign) and isinstance(small.value, cst.Call):
                    callee = _EMPTY.code_for_node(small.value.func)
                    if callee.endswith("get_current_context"):
                        for t in small.targets:
                            if isinstance(t.target, cst.Name):
                                self.from_current_context.add(t.target.value)

    def is_context(self, name: str) -> bool:
        return name == self.star_kwargs or name in self.from_current_context or (
            name == "context" and name in self.params)


def _keyword_args(module: cst.Module, keyword: str) -> list[cst.Arg]:
    """Every `keyword=...` argument in the module's calls."""
    found: list[cst.Arg] = []

    class _Find(cst.CSTVisitor):
        def visit_Arg(self, node: cst.Arg) -> None:
            if node.keyword is not None and node.keyword.value == keyword:
                found.append(node)

    module.visit(_Find())
    return found


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

    def visit_Module(self, node: cst.Module) -> None:
        # functions this file hands to Airflow as callables: only those get context keys by parameter name
        self.callables = {_EMPTY.code_for_node(a.value).rsplit(".", 1)[-1] for a in _keyword_args(node, "python_callable")}

    def _may_be_callable(self, node: cst.FunctionDef, scope: _FunctionScope) -> bool:
        """Whether Airflow may call this function with the task context as keyword arguments: it's passed as a
        python_callable here, it's an @task, or it takes **kwargs and isn't a method (it may be passed elsewhere)."""
        first = next(iter((*node.params.posonly_params, *node.params.params)), None)
        if first is not None and first.name.value in ("self", "cls"):
            return False
        decorators = {_EMPTY.code_for_node(d.decorator).split("(")[0] for d in node.decorators}
        return (node.name.value in self.callables or any(d == "task" or d.startswith("task.") for d in decorators)
                or scope.star_kwargs is not None)

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        scope = _FunctionScope(node)
        self.scopes.append(scope)
        removed = set(self.rename) | set(self.expressions) | set(self.judgment)
        for name in sorted(scope.params & removed if self._may_be_callable(node, scope) else ()):
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

    def rewrite(self, text: str, forbidden_quote: str | None = None, partial: bool = False):
        """Returns (new_text, deterministic_hits, llm_hits); hits are (offset, snippet, reason).

        `partial`: `text` is one piece of a template built by concatenation ("{{ a(" + x + ") }}"), so a
        `}}` with no `{{` before it closes a template, and a `{{` with no `}}` after it opens one."""
        spans = [(m.start(), m.end()) for m in _JINJA.finditer(text)]
        if partial:
            head = text[:spans[0][0] if spans else len(text)]
            close = min((i for i in (head.find("}}"), head.find("%}")) if i >= 0), default=-1)
            if close >= 0:
                spans.insert(0, (0, close + 2))
            start = spans[-1][1] if spans else 0
            opens = [i for i in (text.find("{{", start), text.find("{%", start)) if i >= 0]
            if opens:
                spans.append((min(opens), len(text)))
        det, llm, out, last = [], [], [], 0
        for a, b in spans:
            out.append(text[last:a])
            out.append(self._block(text[a:b], a, forbidden_quote, det, llm))
            last = b
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
        if "b" in prefix or "f" in prefix or not any(t in original.value for t in ("{{", "{%", "}}", "%}")):
            return updated
        quote = original.quote
        inner = original.value[len(original.prefix) + len(quote): len(original.value) - len(quote)]
        # a literal may be one piece of a template built by concatenation
        new_inner, det, llm = self.rw.rewrite(inner, forbidden_quote=None if len(quote) == 3 else quote, partial=True)
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


# --------------------------------------------------------------------------
# Airflow 3: pin cron DAGs to Airflow 2's timetable

CRON_PRESETS = {"@hourly", "@daily", "@weekly", "@monthly", "@quarterly", "@yearly", "@annually", "@midnight"}
_CRON = re.compile(r"^\s*(\S+\s+){4,5}\S+\s*$")  # 5 (or 6) cron fields
_TIMEDELTA = ("timedelta", "datetime.timedelta", "relativedelta", "dateutil.relativedelta.relativedelta")


def _callee(node: cst.Call) -> str:
    return _EMPTY.code_for_node(node.func)


def _kw(call: cst.Call, name: str):
    return next((a.value for a in call.args if a.keyword is not None and a.keyword.value == name), None)


NAIVE = "naive start_date"
_UNKNOWN = object()  # an expression whose value can't be read off the source


def _root_call(node):
    """pendulum.today("UTC").add(days=-1) -> the pendulum.today("UTC") call it starts from."""
    while isinstance(node, cst.Call) and isinstance(node.func, cst.Attribute) and isinstance(node.func.value, cst.Call):
        node = node.func.value
    return node


def _literal_timezone(start) -> str | None:
    """The timezone Airflow 2 would schedule in, if it can be read off a literal start_date (NAIVE: the default)."""
    if isinstance(start, cst.SimpleString):  # parsed by Airflow; an explicit offset isn't handled here
        value = start.evaluated_value
        return None if re.search(r"(Z|[+-]\d\d:?\d\d)$", value) else NAIVE
    if not isinstance(start, cst.Call):
        return None
    start = _root_call(start)
    callee = _callee(start)
    if callee.split(".")[-1] == "days_ago" and callee in ("days_ago", "dates.days_ago", "airflow.utils.dates.days_ago"):
        return "UTC"  # timezone.utcnow() based
    if callee in ("pendulum.today", "pendulum.now", "pendulum.yesterday", "pendulum.tomorrow"):
        tz = start.args[0].value if start.args and start.args[0].keyword is None else _kw(start, "tz")
        # no zone: pendulum's local zone, i.e. the parsing machine's, which can't be known here
        return tz.evaluated_value if isinstance(tz, cst.SimpleString) else None
    if callee in ("datetime.datetime", "datetime", "dt.datetime"):
        # naive datetime: Airflow uses [core] default_timezone
        return None if _kw(start, "tzinfo") is not None or len(start.args) > 7 else NAIVE
    if callee in ("pendulum.datetime", "datetime"):
        tz = _kw(start, "tz")
        if tz is None:
            return "UTC"  # pendulum's default
        return tz.evaluated_value if isinstance(tz, cst.SimpleString) else None
    return None


class _ModuleValues(cst.CSTVisitor):
    """Module-level `NAME = <expr>` assigned exactly once and never mutated: their values can be read."""

    def __init__(self):
        self.values: dict[str, object] = {}
        self.depth = 0

    def visit_FunctionDef(self, node):
        self.depth += 1

    def leave_FunctionDef(self, node):
        self.depth -= 1

    def visit_ClassDef(self, node):
        self.depth += 1

    def leave_ClassDef(self, node):
        self.depth -= 1

    def _taint(self, name):
        self.values[name] = _UNKNOWN

    def visit_Assign(self, node: cst.Assign):
        for t in node.targets:
            if isinstance(t.target, cst.Name):
                name = t.target.value
                self.values[name] = node.value if self.depth == 0 and name not in self.values else _UNKNOWN
            elif isinstance(t.target, cst.Subscript) and isinstance(t.target.value, cst.Name):
                self._taint(t.target.value.value)  # default_args["start_date"] = ...

    def visit_AugAssign(self, node: cst.AugAssign):
        if isinstance(node.target, cst.Name):
            self._taint(node.target.value)  # default_args |= {...}

    def visit_Call(self, node: cst.Call):
        f = node.func
        if isinstance(f, cst.Attribute) and isinstance(f.value, cst.Name) and f.attr.value in (
                "update", "setdefault", "pop", "__setitem__", "clear", "popitem"):
            self._taint(f.value.value)


def _start_from_args(args, values) -> object:
    """default_args' start_date: an expression, None (no such key) or _UNKNOWN."""
    if isinstance(args, cst.Name):
        args = values.get(args.value, _UNKNOWN)
    if isinstance(args, cst.Dict):
        if any(isinstance(e, cst.StarredDictElement) for e in args.elements):
            return _UNKNOWN
        found = None
        for e in args.elements:
            key = _string_key(e.key)
            if key is None:
                return _UNKNOWN
            if key == "start_date":
                found = e.value
        return found
    if isinstance(args, cst.Call) and _callee(args) == "dict":
        if any(a.keyword is None for a in args.args):
            return _UNKNOWN
        return _kw(args, "start_date")
    return _UNKNOWN


def _dag_timezone(call: cst.Call, values: dict) -> str | None:
    """Airflow 2's DAG.__init__: an aware start_date's zone, else default_args' start_date's (its own
    zone, or default_timezone if naive), else default_timezone for a naive one. None: can't tell."""
    def resolve(expr):
        if isinstance(expr, cst.Name):
            expr = values.get(expr.value, _UNKNOWN)
        return None if expr is _UNKNOWN else _literal_timezone(expr)

    start = _kw(call, "start_date")
    own = resolve(start) if start is not None else None
    if start is not None and own is None:
        return None
    if own not in (None, NAIVE):
        return own
    args = _kw(call, "default_args")
    if args is not None:
        inherited = _start_from_args(args, values)
        if inherited is _UNKNOWN:
            return None
        if inherited is not None:
            return resolve(inherited)
    return NAIVE if own == NAIVE else None


SHIFT = "run dates shift by one interval on Airflow 3 unless pinned"


class _CronPin(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, rewrite: bool, options: dict):
        self.rewrite = rewrite
        self.default_tz = options.get("default_timezone", "UTC")
        self.tz_problem = options.get("timezone_problem", "")
        self.det: list[Site] = []
        self.llm: list[Site] = []
        self.pinned = 0

    def _site(self, node, bucket, reason=""):
        bucket.append(Site(self.get_metadata(PositionProvider, node).start.line, _EMPTY.code_for_node(node), reason))

    def visit_Module(self, node: cst.Module):
        scan = _ModuleValues()
        node.visit(scan)
        self.values = scan.values

    def leave_Call(self, original: cst.Call, updated: cst.Call):
        if not (_callee(original).split(".")[-1] == "DAG" or _callee(original) == "dag"):
            return updated
        idx = next((i for i, a in enumerate(original.args)
                    if a.keyword is not None and a.keyword.value in ("schedule", "schedule_interval")), None)
        if idx is None:
            return updated
        value = original.args[idx].value
        if isinstance(value, cst.SimpleString):
            cron = value.evaluated_value
            if cron not in CRON_PRESETS and not _CRON.match(cron):
                return updated  # @once, @continuous, ...
            tz = _dag_timezone(original, self.values)
            if tz is None:
                self._site(value, self.llm, f"{SHIFT}: cron schedule, but the DAG's timezone can't be read off a "
                                            "literal start_date (on the DAG or in default_args); pin "
                                            "CronDataIntervalTimetable(cron, timezone=...) by hand")
                return updated
            reason = ""
            if tz == NAIVE:
                if not self.default_tz:
                    self._site(value, self.llm, f"{SHIFT}: cron schedule with a naive start_date, but "
                                                f"{self.tz_problem}")
                    return updated
                tz, reason = self.default_tz, f"{NAIVE}: [core] default_timezone = {self.default_tz}"
            self._site(value, self.det, reason)
            if not self.rewrite:
                return updated
            self.pinned += 1
            pinned = cst.parse_expression(f'CronDataIntervalTimetable({value.value}, timezone="{tz}")')
            arg = updated.args[idx].with_changes(keyword=cst.Name("schedule"), value=pinned)
            return updated.with_changes(args=[*updated.args[:idx], arg, *updated.args[idx + 1:]])
        if isinstance(value, cst.Call) and _callee(value) in _TIMEDELTA:
            self._site(value, self.llm, f"{SHIFT}: timedelta schedule; Airflow 3 also changed the default for "
                                        "these ([scheduler] create_delta_data_intervals = False); pin "
                                        "DeltaDataIntervalTimetable to keep Airflow 2's run dates")
        elif isinstance(value, cst.Attribute) or (isinstance(value, cst.Name) and value.value != "None"):
            self._site(value, self.llm, f"{SHIFT} if this is a cron string or timedelta: the schedule is not a "
                                        "literal; pin it to CronDataIntervalTimetable / DeltaDataIntervalTimetable")
        return updated

    def leave_Module(self, original: cst.Module, updated: cst.Module):
        if not self.pinned or "CronDataIntervalTimetable" in _imported_names(original):
            return updated
        body = list(updated.body)
        for i, st in enumerate(body):  # join an existing `from airflow.sdk import ...` rather than add a second one
            if (isinstance(st, cst.SimpleStatementLine) and len(st.body) == 1 and isinstance(st.body[0], cst.ImportFrom)
                    and st.body[0].module is not None and _EMPTY.code_for_node(st.body[0].module) == "airflow.sdk"
                    and isinstance(st.body[0].names, (list, tuple)) and not st.body[0].lpar):
                names = [*st.body[0].names[:-1], st.body[0].names[-1].with_changes(comma=cst.Comma(
                    whitespace_after=cst.SimpleWhitespace(" "))), cst.ImportAlias(name=cst.Name("CronDataIntervalTimetable"))]
                body[i] = st.with_changes(body=[st.body[0].with_changes(names=names)])
                return updated.with_changes(body=body)
        last = max((i for i, st in enumerate(body) if isinstance(st, cst.SimpleStatementLine)
                    and any(isinstance(x, (cst.Import, cst.ImportFrom)) for x in st.body)), default=-1)
        imp = cst.parse_statement("from airflow.sdk import CronDataIntervalTimetable\n")
        body.insert(last + 1, imp)
        return updated.with_changes(body=body)


def _imported_names(module: cst.Module) -> set[str]:
    names = set()
    for st in module.body:
        if not isinstance(st, cst.SimpleStatementLine):
            continue
        for small in st.body:
            if isinstance(small, cst.ImportFrom) and not isinstance(small.names, cst.ImportStar):
                names |= {(a.asname.name.value if a.asname else _EMPTY.code_for_node(a.name)) for a in small.names}
    return names


class AirflowCronPin:
    """`schedule="@daily"` -> `schedule=CronDataIntervalTimetable("@daily", timezone=...)` (Airflow 2 semantics)."""

    name = "airflow_cron_pin"
    file_globs: tuple[str, ...] = ()

    def _run(self, text: str, rewrite: bool, options: dict):
        t = _CronPin(rewrite, options)
        return MetadataWrapper(cst.parse_module(text)).visit(t).code, t

    def find(self, path: Path, text: str, options: dict, mode: str) -> list[Site]:
        if path.suffix != ".py":
            return []
        _, t = self._run(text, rewrite=False, options=options)
        return t.det if mode == "deterministic" else t.llm

    def apply(self, path: Path, text: str, options: dict) -> str:
        return text if path.suffix != ".py" else self._run(text, rewrite=True, options=options)[0]


# --------------------------------------------------------------------------
# Airflow 3: airflow.utils.dates.days_ago was removed

# days_ago(n, hour, minute, second, microsecond) =
#   timezone.utcnow().replace(hour=.., minute=.., second=.., microsecond=..) - timedelta(days=n)
# i.e. UTC midnight today (+ the given time of day) minus n days, evaluated when the DAG file is parsed.
# pendulum.today("UTC") is UTC midnight today; in UTC, add(days=-n, hours=h, ...) is the same arithmetic.
_DAYS_AGO_PARTS = {"hour": ("hours", 23), "minute": ("minutes", 59), "second": ("seconds", 59),
                   "microsecond": ("microseconds", 999_999)}


def _int_literal(node) -> int | None:
    if isinstance(node, cst.Integer):
        return int(node.value.replace("_", ""), 0)
    if (isinstance(node, cst.UnaryOperation) and isinstance(node.operator, cst.Minus)
            and isinstance(node.expression, cst.Integer)):
        return -int(node.expression.value.replace("_", ""), 0)
    return None


class _DaysAgoScan(cst.CSTVisitor):
    """Where days_ago comes from in this file, and whether the name `pendulum` is free to use."""

    def __init__(self):
        self.names: set[str] = set()      # local names bound to days_ago
        self.modules: set[str] = set()    # names bound to the airflow.utils.dates module
        self.nested_import = False
        self.pendulum_imported = False
        self.pendulum_taken = False
        self.depth = 0
        self.refs = 0                     # references to a days_ago name...
        self.calls = 0                    # ...and how many of them are calls

    def visit_FunctionDef(self, node):
        self.depth += 1

    def leave_FunctionDef(self, node):
        self.depth -= 1

    def visit_ImportFrom(self, node: cst.ImportFrom):
        module = _EMPTY.code_for_node(node.module) if node.module else ""
        if isinstance(node.names, cst.ImportStar):
            return False
        for a in node.names:
            bound = a.asname.name.value if a.asname else _EMPTY.code_for_node(a.name)
            if (module, _EMPTY.code_for_node(a.name)) in (("airflow.utils.dates", "days_ago"), ("airflow.utils", "dates")):
                (self.names if module == "airflow.utils.dates" else self.modules).add(bound)
                self.nested_import |= self.depth > 0
            elif bound == "pendulum":
                self.pendulum_taken = True
        return False

    def visit_Import(self, node: cst.Import):
        for a in node.names:
            name = _EMPTY.code_for_node(a.name)
            bound = a.asname.name.value if a.asname else name.split(".")[0]
            if name == "airflow.utils.dates":
                self.modules.add(a.asname.name.value if a.asname else name)
                self.nested_import |= self.depth > 0
            elif bound == "pendulum":
                if name == "pendulum" and not a.asname:
                    self.pendulum_imported = True
                else:
                    self.pendulum_taken = True
        return False

    def visit_Name(self, node: cst.Name):
        if node.value in self.names:
            self.refs += 1

    def visit_AssignTarget(self, node: cst.AssignTarget):
        if isinstance(node.target, cst.Name) and node.target.value == "pendulum":
            self.pendulum_taken = True

    def visit_Call(self, node: cst.Call):
        if isinstance(node.func, cst.Name) and node.func.value in self.names:
            self.calls += 1


class _DaysAgo(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, rewrite: bool, scan: _DaysAgoScan):
        self.rewrite, self.scan = rewrite, scan
        self.det: list[Site] = []
        self.llm: list[Site] = []
        self.rewritten = 0

    def _is_days_ago(self, call: cst.Call) -> bool:
        f = call.func
        if isinstance(f, cst.Name):
            return f.value in self.scan.names
        if isinstance(f, cst.Attribute) and f.attr.value == "days_ago":
            owner = _EMPTY.code_for_node(f.value)
            return owner in self.scan.modules or owner == "airflow.utils.dates"
        return False

    def _replacement(self, call: cst.Call) -> tuple[str | None, str]:
        """(pendulum expression, why not) for one days_ago(...) call."""
        n, parts = None, {}
        for i, a in enumerate(call.args):
            if a.star:
                return None, "days_ago called with *args/**kwargs"
            key = a.keyword.value if a.keyword else ("n" if i == 0 else None)
            if key == "n":
                n = _int_literal(a.value)
                if n is None:
                    return None, "days_ago's day count is not a literal integer"
            elif key in _DAYS_AGO_PARTS:
                v = _int_literal(a.value)
                if v is None or not 0 <= v <= _DAYS_AGO_PARTS[key][1]:
                    return None, f"days_ago's {key}= is not a literal in range"
                parts[key] = v
            else:
                return None, f"days_ago called with an unexpected argument {key!r}"
        if n is None:
            return None, "days_ago called without a day count"
        extra = "".join(f", {_DAYS_AGO_PARTS[k][0]}={parts[k]}" for k in _DAYS_AGO_PARTS if k in parts)
        return f'pendulum.today("UTC").add(days={-n}{extra})', ""

    def leave_Call(self, original: cst.Call, updated: cst.Call):
        if not self._is_days_ago(original):
            return updated
        line = self.get_metadata(PositionProvider, original).start.line
        text = _EMPTY.code_for_node(original)
        expr, why = self._replacement(original)
        blocked = ("days_ago is used other than as a call" if self.scan.refs > self.scan.calls else
                   "days_ago is imported inside a function" if self.scan.nested_import else
                   "the name `pendulum` is bound to something else in this file" if self.scan.pendulum_taken else "")
        if expr is None or blocked:
            self.llm.append(Site(line, text, (
                f"{why or blocked}; Airflow 3 removed days_ago: replace with pendulum.today(\"UTC\").add(days=-N), "
                "the same moment (UTC midnight today minus N days, evaluated at parse time). Not a fixed date: "
                "that changes scheduling and catchup")))
            return updated
        self.det.append(Site(line, text, ""))
        if not self.rewrite:
            return updated
        self.rewritten += 1
        return cst.parse_expression(expr)

    def leave_Module(self, original: cst.Module, updated: cst.Module):
        if not self.rewritten:
            return updated
        body, placed = [], self.scan.pendulum_imported
        pendulum_import = cst.parse_statement("import pendulum\n")
        for st in updated.body:
            new = self._drop_days_ago_imports(st, updated)
            if new is None:
                if not placed:
                    body.append(pendulum_import.with_changes(leading_lines=st.leading_lines))
                    placed = True
                continue
            body.append(new)
        if not placed:
            last = max((i for i, st in enumerate(body) if isinstance(st, cst.SimpleStatementLine)
                        and any(isinstance(x, (cst.Import, cst.ImportFrom)) for x in st.body)), default=-1)
            body.insert(last + 1, pendulum_import)
        return updated.with_changes(body=body)

    def _drop_days_ago_imports(self, st, module: cst.Module):
        """The statement without the days_ago / dates imports nothing uses any more (None: drop it)."""
        if not isinstance(st, cst.SimpleStatementLine) or len(st.body) != 1:
            return st
        node = st.body[0]
        rest = module.code.replace(module.code_for_node(st), "", 1)
        def unused(bound: str) -> bool:
            return not re.search(rf"(?<![\w.]){re.escape(bound)}\b", rest)
        if isinstance(node, cst.ImportFrom) and not isinstance(node.names, cst.ImportStar) and node.module is not None:
            module_name = _EMPTY.code_for_node(node.module)
            keep = []
            for a in node.names:
                bound = a.asname.name.value if a.asname else _EMPTY.code_for_node(a.name)
                ours = (module_name, _EMPTY.code_for_node(a.name)) in (("airflow.utils.dates", "days_ago"),
                                                                      ("airflow.utils", "dates"))
                if not (ours and unused(bound)):
                    keep.append(a)
            if len(keep) == len(node.names):
                return st
            if not keep:
                return None
            keep[-1] = keep[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
            return st.with_changes(body=[node.with_changes(names=keep)])
        if isinstance(node, cst.Import):
            keep = [a for a in node.names if not (_EMPTY.code_for_node(a.name) == "airflow.utils.dates"
                                                    and unused(a.asname.name.value if a.asname else "airflow.utils.dates"))]
            if len(keep) == len(node.names):
                return st
            if not keep:
                return None
            keep[-1] = keep[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
            return st.with_changes(body=[node.with_changes(names=keep)])
        return st


class AirflowDaysAgo:
    """`days_ago(N)` -> `pendulum.today("UTC").add(days=-N)`: the same instant, still computed at parse time."""

    name = "airflow_days_ago"
    file_globs: tuple[str, ...] = ()

    def _run(self, text: str, rewrite: bool):
        module = cst.parse_module(text)
        scan = _DaysAgoScan()
        module.visit(scan)
        if not scan.names and not scan.modules and "airflow.utils.dates.days_ago" not in text:
            return text, _DaysAgo(False, scan)
        t = _DaysAgo(rewrite, scan)
        return MetadataWrapper(module).visit(t).code, t

    def find(self, path: Path, text: str, options: dict, mode: str) -> list[Site]:
        if path.suffix != ".py" or "days_ago" not in text:
            return []
        _, t = self._run(text, rewrite=False)
        return t.det if mode == "deterministic" else t.llm

    def apply(self, path: Path, text: str, options: dict) -> str:
        if path.suffix != ".py" or "days_ago" not in text:
            return text
        return self._run(text, rewrite=True)[0]


# --------------------------------------------------------------------------
# Airflow 3: DAG methods that were removed (check only)


class _DagNames(cst.CSTVisitor):
    """Names bound to a DAG in this file: `x = DAG(...)`, `with DAG(...) as x`."""

    def __init__(self):
        self.names = {"dag"}

    def visit_Assign(self, node: cst.Assign):
        if isinstance(node.value, cst.Call) and _callee(node.value).split(".")[-1] == "DAG":
            self.names |= {t.target.value for t in node.targets if isinstance(t.target, cst.Name)}

    def visit_WithItem(self, node: cst.WithItem):
        if (isinstance(node.item, cst.Call) and _callee(node.item).split(".")[-1] == "DAG"
                and node.asname is not None and isinstance(node.asname.name, cst.Name)):
            self.names.add(node.asname.name.value)


class _RemovedDagMethods(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, options: dict):
        self.methods = set(options.get("methods", ()))
        self.any_receiver = set(options.get("any_receiver", ()))
        self.hints = options.get("hints", {})
        self.sites: list[Site] = []
        self.dag_names: set[str] = {"dag"}

    def visit_Module(self, node: cst.Module):
        names = _DagNames()
        node.visit(names)
        self.dag_names = names.names

    def visit_Import(self, node):
        return False

    def visit_ImportFrom(self, node):
        return False

    def _is_dag(self, expr) -> bool:
        if isinstance(expr, cst.Name):
            return expr.value in self.dag_names
        if isinstance(expr, cst.Attribute):
            return expr.attr.value == "dag"                                   # task.dag, self.dag
        if isinstance(expr, cst.Subscript):
            return _subscript_key(expr) == "dag"                             # context["dag"]
        if isinstance(expr, cst.Call):
            f = expr.func
            if isinstance(f, cst.Attribute) and f.attr.value == "get" and expr.args:
                return _string_key(expr.args[0].value) == "dag"              # kwargs.get("dag")
            return _callee(expr).split(".")[-1] == "DAG"
        return False

    def visit_Attribute(self, node: cst.Attribute):
        name = node.attr.value
        if name not in self.methods or not (name in self.any_receiver or self._is_dag(node.value)):
            return
        hint = self.hints.get(name)
        reason = (f"DAG.{name}() was removed in Airflow 3 (task code can no longer reach the scheduler's "
                  f"database); {hint or 'there is no direct replacement'}. The file still imports, so only a "
                  "run of this code fails")
        self.sites.append(Site(self.get_metadata(PositionProvider, node).start.line, _EMPTY.code_for_node(node), reason))


class AirflowRemovedDagMethods:
    """Uses of DAG methods Airflow 3 removed. Check only: every site is for a human."""

    name = "airflow_removed_dag_methods"
    file_globs: tuple[str, ...] = ()

    def find(self, path: Path, text: str, options: dict, mode: str) -> list[Site]:
        if path.suffix != ".py" or mode == "deterministic":
            return []
        v = _RemovedDagMethods(options)
        MetadataWrapper(cst.parse_module(text)).visit(v)
        return v.sites

    def apply(self, path: Path, text: str, options: dict) -> str:
        return text


# --------------------------------------------------------------------------
# Airflow 3: names airflow modules re-exported from elsewhere


class _Reexports(cst.CSTTransformer):
    """`from airflow.utils.dates import datetime` -> `from datetime import datetime` (the same object)."""

    def __init__(self, table: dict[str, dict[str, list[str]]]):
        # {airflow module: {real module: [names]}} -> {airflow module: {name: real module}}
        self.table = {mod: {n: real for real, names in by.items() for n in names} for mod, by in table.items()}
        self.changed = 0

    def leave_SimpleStatementLine(self, original, updated):
        if len(updated.body) != 1 or not isinstance(updated.body[0], cst.ImportFrom):
            return updated
        node = updated.body[0]
        if node.module is None or isinstance(node.names, cst.ImportStar):
            return updated
        names = self.table.get(_EMPTY.code_for_node(node.module))
        if not names:
            return updated
        keep, moved = [], {}
        for a in node.names:
            real = names.get(_EMPTY.code_for_node(a.name))
            (moved.setdefault(real, []) if real else keep).append(a)
        if not moved:
            return updated
        self.changed += 1
        out = []
        if keep:
            keep[-1] = keep[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
            out.append(updated.with_changes(body=[node.with_changes(names=keep)]))
        for real, aliases in moved.items():
            aliases[-1] = aliases[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
            stmt = cst.SimpleStatementLine(body=[cst.ImportFrom(
                module=cst.parse_expression(real), names=[a.with_changes(comma=cst.MaybeSentinel.DEFAULT) if i == len(aliases) - 1
                                                         else a for i, a in enumerate(aliases)])])
            out.append(stmt.with_changes(leading_lines=updated.leading_lines) if not out else stmt)
        return cst.FlattenSentinel(out)


class AirflowReexports:
    """Imports of names an airflow module only re-exported, from the module that defines them."""

    name = "airflow_reexports"
    file_globs: tuple[str, ...] = ()

    def _run(self, text: str, options: dict):
        t = _Reexports(options)
        return cst.parse_module(text).visit(t).code, t

    def find(self, path: Path, text: str, options: dict, mode: str) -> list[Site]:
        if path.suffix != ".py" or mode != "deterministic" or not any(m in text for m in options):
            return []
        new, t = self._run(text, options)
        return [Site(1, "", "")] if t.changed else []

    def apply(self, path: Path, text: str, options: dict) -> str:
        if path.suffix != ".py" or not any(m in text for m in options):
            return text
        return self._run(text, options)[0]


# --------------------------------------------------------------------------
# Airflow 3: operator keyword arguments that were removed or renamed


class _OperatorKwargs(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, options: dict, rewrite: bool):
        self.drop = options.get("drop", {})                    # kwarg -> why it's safe to drop
        self.rename = options.get("rename", {})                # kwarg -> new name, any operator
        self.rename_for = options.get("rename_for", {})        # class -> {kwarg -> new name}
        self.rewrite = rewrite
        self.det: list[Site] = []
        self.llm: list[Site] = []

    def leave_Call(self, original: cst.Call, updated: cst.Call):
        keywords = {a.keyword.value for a in original.args if a.keyword is not None}
        if "task_id" not in keywords:
            return updated  # not an operator
        cls = _callee(original).split(".")[-1]
        renames = {**self.rename, **self.rename_for.get(cls, {})}
        line = self.get_metadata(PositionProvider, original).start.line
        args, changed = [], False
        for a in updated.args:
            kw = a.keyword.value if a.keyword is not None else None
            if kw in self.drop:
                self.det.append(Site(line, kw, self.drop[kw]))
                changed = True
                continue
            if kw in renames:
                new = renames[kw]
                if new in keywords:
                    self.llm.append(Site(line, kw, f"`{kw}=` was renamed `{new}=` in Airflow 3, and this call passes "
                                                   "both; keep the one you mean"))
                else:
                    self.det.append(Site(line, kw, f"renamed `{new}=`"))
                    a = a.with_changes(keyword=cst.Name(new))
                    changed = True
            args.append(a)
        if not (changed and self.rewrite):
            return updated
        if args and updated.args and updated.args[-1].comma == cst.MaybeSentinel.DEFAULT:
            args[-1] = args[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)  # no trailing comma where none was
        return updated.with_changes(args=args)


class AirflowOperatorKwargs:
    """Operator kwargs Airflow 3 dropped (no-ops before) or renamed, in calls that pass `task_id=`."""

    name = "airflow_operator_kwargs"
    file_globs: tuple[str, ...] = ()

    def _keys(self, options: dict) -> set[str]:
        return set(options.get("drop", {})) | set(options.get("rename", {})) | {
            k for by in options.get("rename_for", {}).values() for k in by}

    def _run(self, text: str, options: dict, rewrite: bool):
        t = _OperatorKwargs(options, rewrite)
        return MetadataWrapper(cst.parse_module(text)).visit(t).code, t

    def find(self, path: Path, text: str, options: dict, mode: str) -> list[Site]:
        if path.suffix != ".py" or not any(k in text for k in self._keys(options)):
            return []
        _, t = self._run(text, options, rewrite=False)
        return t.det if mode == "deterministic" else t.llm

    def apply(self, path: Path, text: str, options: dict) -> str:
        if path.suffix != ".py" or not any(k in text for k in self._keys(options)):
            return text
        return self._run(text, options, rewrite=True)[0]


PLUGINS = {p.name: p for p in (AirflowContextKeys(), AirflowTemplates(), AirflowCronPin(), AirflowDaysAgo(),
                               AirflowRemovedDagMethods(), AirflowReexports(), AirflowOperatorKwargs())}
