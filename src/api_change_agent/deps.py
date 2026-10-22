"""Declare a dependency the repo imports but never listed.

Used by ``add_dependency`` rules (e.g. httpx, which openai 3.0 stopped
installing). The new requirement goes into the same file, and the same
list, as the SDK's own declaration, written in that file's style.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from packaging.requirements import Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from .pins import INTENT_RE, _is_requirements_file, _req_regex, _walk, comment_of

Kind = Literal["req", "cfg", "quoted", "poetry"]

_POETRY_TABLE = re.compile(r"^\[tool\.poetry\.(dependencies|dev-dependencies|group\.[^.\]]+\.dependencies)\]$")
_LIST_TABLES = ("[project.optional-dependencies]", "[dependency-groups]")
_CFG_KEYS = ("install_requires", "tests_require", "setup_requires")
_KEY = re.compile(r"^\s*([\w.-]+|\"[^\"]+\")\s*=")
_LOCKS = ("poetry.lock", "uv.lock", "pdm.lock")


@dataclass(frozen=True)
class Declaration:
    path: Path
    line: int
    kind: Kind
    spec: str  # version constraint as written ("" = unpinned)
    text: str  # the declaration as it appears
    span: tuple[int, int] = (0, 0)  # column span of a quoted string, quotes included


@dataclass(frozen=True)
class DepEdit:
    path: Path
    line: int
    text: str
    new_line: bool = True  # False when added inline to an existing list line


@dataclass(frozen=True)
class DepProblem:
    path: Path
    line: int
    reason: str


@dataclass
class DependencyPlan:
    requirement: str
    declared: list[Declaration] = field(default_factory=list)  # dependency already listed
    sdk: list[Declaration] = field(default_factory=list)  # every SDK declaration
    triggering: list[Declaration] = field(default_factory=list)  # SDK pins in range
    holds: list[DepProblem] = field(default_factory=list)  # "intentionally not declared" comments
    problems: list[DepProblem] = field(default_factory=list)  # files we must not edit
    edits: list[DepEdit] = field(default_factory=list)
    files: dict[Path, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# reading declarations


def _dependency_files(root: Path):
    for rel in _walk(root):
        if _is_requirements_file(rel) or rel.name in ("setup.cfg", "setup.py", "pyproject.toml"):
            yield rel


def declarations(root: Path, name: str) -> list[Declaration]:
    found: list[Declaration] = []
    for rel in _dependency_files(Path(root)):
        found += _scan(Path(root), rel, name)
    return found


def _scan(root: Path, rel: Path, name: str) -> list[Declaration]:
    text = (root / rel).read_text(errors="replace")
    if name.lower() not in text.lower():
        return []
    req = _req_regex(name)
    out: list[Declaration] = []
    table = ""
    in_cfg_list = False
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("[") and rel.name in ("pyproject.toml", "setup.cfg"):
            table = stripped.split("#")[0].strip()
            in_cfg_list = False
            continue
        if not stripped or stripped.startswith(("#", ";")):
            continue
        key_m = _KEY.match(line)
        key = key_m.group(1).strip('"') if key_m else None

        if _is_requirements_file(rel) or rel.name == "setup.cfg":
            if stripped.startswith("-"):
                continue
            if rel.name == "setup.cfg":
                if key is not None:
                    in_cfg_list = key in _CFG_KEYS or table == "[options.extras_require]"
                    if not in_cfg_list:
                        continue
                elif not (in_cfg_list and line[:1].isspace()):
                    continue
            lead = key_m.end() if (rel.name == "setup.cfg" and key_m) else 0
            lead += len(line[lead:]) - len(line[lead:].lstrip())
            m = req.match(line, lead)
            if m:
                out.append(Declaration(rel, i, "cfg" if rel.name == "setup.cfg" else "req",
                                       (m.group("spec") or "").strip(), m.group("req").strip()))
            continue

        if rel.name == "pyproject.toml" and _POETRY_TABLE.match(table):
            if key is not None and key.lower() == name.lower():
                value = line[key_m.end():].strip()
                spec_m = re.match(r"([\"'])(?P<s>[^\"']*)\1", value) or re.search(
                    r"\bversion\s*=\s*([\"'])(?P<s>[^\"']*)\1", value)
                spec = spec_m.group("s") if spec_m else ""
                out.append(Declaration(rel, i, "poetry", "" if spec == "*" else spec, stripped))
            continue

        if rel.name == "pyproject.toml":
            if table == "[project]":
                if key not in (None, "dependencies"):
                    continue
            elif table not in _LIST_TABLES:
                continue
        for q in re.finditer(r"([\"'])([^\"'\n]*)\1", line):
            inner = q.group(2).strip()
            m = req.match(inner)
            if m and m.end() == len(inner.split(";")[0].rstrip()):
                out.append(Declaration(rel, i, "quoted", (m.group("spec") or "").strip(), inner, q.span()))
    return out


# --------------------------------------------------------------------------
# does a pin allow the target range?


def _poetry_to_pep440(spec: str) -> str:
    parts = []
    for clause in spec.split(","):
        c = clause.strip()
        if c in ("", "*"):
            continue
        if c.startswith("^") or (c.startswith("~") and not c.startswith("~=")):
            base = c[1:].strip()
            rel = list(Version(base).release)
            if c.startswith("^"):
                idx = next((k for k, x in enumerate(rel) if x != 0), len(rel) - 1)
                upper = rel[:idx] + [rel[idx] + 1]
            else:
                upper = [rel[0] + 1] if len(rel) == 1 else [rel[0], rel[1] + 1]
            parts += [f">={base}", f"<{'.'.join(map(str, upper))}"]
        elif c[0].isdigit():
            parts.append(f"=={c}")
        else:
            parts.append(c)
    return ",".join(parts)


def spec_allows(spec: str, target: SpecifierSet, poetry: bool = False) -> bool:
    """True if some version satisfies both ``spec`` and ``target``."""
    try:
        allowed = SpecifierSet(_poetry_to_pep440(spec) if poetry else spec)
    except (InvalidSpecifier, InvalidVersion, IndexError):
        return False
    candidates: set[Version] = {Version("999")}
    for s in list(allowed) + list(target):
        raw = s.version
        if raw.endswith(".*"):
            raw = raw[:-2] + ".0"
        try:
            v = Version(raw)
        except InvalidVersion:
            continue
        r = list(v.release) + [0, 0]
        candidates |= {v, Version(f"{r[0]}.{r[1]}.{r[2] + 1}"), Version(f"{r[0]}.{r[1] + 1}.0"),
                       Version(f"{r[0] + 1}.0.0")}
    return any(allowed.contains(c) and target.contains(c) for c in candidates)


# --------------------------------------------------------------------------
# writing


def _insert(lines: list[str], decl: Declaration, requirement: str, name: str) -> tuple[int, str, bool]:
    """Insert ``requirement`` next to ``decl`` in ``lines`` (in place).

    Returns (1-based line of the new text, the text as written, whether a
    whole new line was added)."""
    i = decl.line - 1
    anchor = lines[i]
    body = anchor.rstrip("\r\n")
    nl = anchor[len(body):] or "\n"
    indent = body[: len(body) - len(body.lstrip())]

    if decl.kind in ("req", "cfg"):
        if decl.kind == "cfg" and _KEY.match(body):
            indent = "    "  # "install_requires = openai" -> continuation line
        j = i
        while lines[j].rstrip("\r\n").endswith("\\") and j + 1 < len(lines):
            j += 1  # skip backslash continuations
        lines.insert(j + 1, f"{indent}{requirement}{nl}")
        return j + 2, requirement, True

    if decl.kind == "poetry":
        spec = requirement[len(Requirement(requirement).name):].strip() or "*"
        text = f'{name} = "{spec}"'
        lines.insert(i + 1, f"{indent}{text}{nl}")
        return i + 2, text, True

    # quoted: own line in a multi-line list, otherwise inline after the string
    start, end = decl.span
    quote = body[start]
    rest = body[end:]
    own_line = body[:start].strip() == "" and re.fullmatch(r"\s*,?\s*(#.*)?", rest)
    if own_line:
        has_comma = rest.lstrip().startswith(",")
        if not has_comma:
            comment = rest.strip()
            lines[i] = body[:end] + "," + (f"  {comment}" if comment else "") + nl
        text = f"{quote}{requirement}{quote}"
        lines.insert(i + 1, f"{indent}{text}{',' if has_comma else ''}{nl}")
        return i + 2, text, True
    text = f"{quote}{requirement}{quote}"
    lines[i] = body[:end] + f", {text}" + rest + nl
    return decl.line, text, False


def plan_add_dependency(root: Path, package: str, requirement: str,
                        sdk_allows: SpecifierSet | None) -> DependencyPlan:
    root = Path(root)
    name = Requirement(requirement).name
    plan = DependencyPlan(requirement=requirement)
    plan.declared = declarations(root, name)
    plan.sdk = declarations(root, package)
    plan.triggering = [d for d in plan.sdk
                       if sdk_allows is None or spec_allows(d.spec, sdk_allows, poetry=d.kind == "poetry")]

    word = re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])", re.I)
    for rel in _dependency_files(root):
        for i, line in enumerate((root / rel).read_text(errors="replace").splitlines(), 1):
            comment = line.strip() if line.lstrip().startswith("#") else comment_of(line)
            if comment and word.search(comment) and INTENT_RE.search(comment):
                plan.holds.append(DepProblem(rel, i, comment))
    if plan.declared or plan.holds or not plan.triggering:
        return plan

    seen: set[Path] = set()
    for decl in plan.triggering:
        if decl.path in seen:
            continue
        seen.add(decl.path)
        text = (root / decl.path).read_text()
        if decl.kind == "req" and "--hash" in text:
            plan.problems.append(DepProblem(decl.path, decl.line,
                                            "uses --hash pins; regenerate it with the dependency added"))
            continue
        lines = text.splitlines(keepends=True)
        line, written, new_line = _insert(lines, decl, requirement, name)
        plan.files[decl.path] = "".join(lines)
        plan.edits.append(DepEdit(decl.path, line, written, new_line))
        if decl.path.name == "pyproject.toml":
            for lock in _LOCKS:
                if (root / decl.path.parent / lock).exists():
                    plan.notes.append(f"`{(decl.path.parent / lock).as_posix()}` must be regenerated "
                                      f"after adding `{name}` to `{decl.path.as_posix()}`")
        if decl.path.suffix == ".in" and (root / decl.path.with_suffix(".txt")).exists():
            plan.notes.append(f"`{decl.path.with_suffix('.txt').as_posix()}` is compiled from "
                              f"`{decl.path.as_posix()}`; re-run pip-compile")
    return plan
