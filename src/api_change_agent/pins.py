"""Find and bump the package's version pins in dependency files.

Edits are made on the text so comments and layout survive. Lock files are
never rewritten; a too-old locked version becomes a note in the PR.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from .scanner import SKIP_DIRS

Status = Literal["ok", "bumped", "conflict", "unpinned"]


@dataclass(frozen=True)
class SpecBump:
    new: str
    status: Status
    reason: str = ""


@dataclass(frozen=True)
class PinEdit:
    path: Path
    line: int
    before: str
    after: str


@dataclass(frozen=True)
class PinConflict:
    path: Path
    line: int
    text: str
    reason: str


@dataclass(frozen=True)
class PinHold:
    """A pin that would need bumping but is marked as intentional."""
    path: Path
    line: int
    text: str
    comment: str


@dataclass(frozen=True)
class LockNote:
    path: Path
    version: str


@dataclass
class PinPlan:
    minimum: Version
    edits: list[PinEdit] = field(default_factory=list)
    conflicts: list[PinConflict] = field(default_factory=list)
    holds: list[PinHold] = field(default_factory=list)
    lock_notes: list[LockNote] = field(default_factory=list)
    files: dict[Path, str] = field(default_factory=dict)  # new contents


# --------------------------------------------------------------------------
# specifiers

_CLAUSE_RE = re.compile(r"(?P<lead>\s*)(?P<op>===|==|!=|~=|>=|<=|>|<|\^|~)?(?P<ws>\s*)(?P<ver>\S+?)(?P<trail>\s*)")


def _version(text: str) -> Version | None:
    try:
        return Version(text)
    except InvalidVersion:
        return None


def _same_precision(minimum: Version, like: str) -> str:
    """``minimum`` truncated to as many release parts as ``like`` (min 2),
    unless that would drop below ``minimum``."""
    parts = max(2, len(like.split(".")))
    truncated = ".".join(str(p) for p in minimum.release[:parts])
    return truncated if Version(truncated) >= minimum else str(minimum)


def bump_spec(spec: str, minimum: str | Version, poetry: bool = False) -> SpecBump:
    """Rewrite ``spec`` so it allows no version below ``minimum``."""
    minimum = Version(str(minimum))
    if not spec.strip() or (poetry and spec.strip() == "*"):
        return SpecBump(spec, "unpinned")
    pieces = re.split(r"(\s*,\s*)", spec)
    clauses, seps = pieces[0::2], pieces[1::2]
    out: list[str] = []
    has_lower = False

    def conflict(why: str) -> SpecBump:
        return SpecBump(spec, "conflict", why)

    for clause in clauses:
        m = _CLAUSE_RE.fullmatch(clause)
        if not m:
            return conflict(f"can't parse {clause!r}")
        lead, op, ws, ver_text, trail = m.group("lead", "op", "ws", "ver", "trail")
        if op is None and poetry:
            op_eff = "=="
        elif op is None:
            return conflict(f"can't parse {clause!r}")
        else:
            op_eff = op
        new_clause = clause

        if op_eff == "==" and ver_text.endswith(".*"):
            prefix = ver_text[:-2]
            if SpecifierSet(f"=={ver_text}").contains(minimum):
                pass  # needs a lower bound added below
            elif (_version(prefix) or minimum) < minimum:
                return conflict(f"{op}{ver_text} excludes {minimum}")
            else:
                has_lower = True
        else:
            v = _version(ver_text)
            if v is None:
                return conflict(f"bad version {ver_text!r}")
            if op_eff in (">=", ">"):
                if v < minimum:
                    new_clause = f"{lead}>={ws}{minimum}{trail}"
                has_lower = True
            elif op_eff == "==":
                if v < minimum:
                    new_clause = f"{lead}{op or ''}{ws}{minimum}{trail}"
                has_lower = True
            elif op_eff == "===":
                if v < minimum:
                    return conflict(f"==={ver_text} excludes {minimum}")
                has_lower = True
            elif op_eff in ("~=", "~"):
                if v < minimum:
                    target = _same_precision(minimum, ver_text) if op_eff == "~=" else str(minimum)
                    new_clause = f"{lead}{op}{ws}{target}{trail}"
                has_lower = True
            elif op_eff == "^":
                if v < minimum:
                    new_clause = f"{lead}^{ws}{minimum}{trail}"
                has_lower = True
            elif op_eff == "<":
                if v <= minimum:
                    return conflict(f"<{ver_text} excludes {minimum}")
            elif op_eff == "<=":
                if v < minimum:
                    return conflict(f"<={ver_text} excludes {minimum}")
            # "!=" never decides anything here
        out.append(new_clause)

    new = out[0] + "".join(s + c for s, c in zip(seps, out[1:]))
    if not has_lower:
        new = f"{new.rstrip()},>={minimum}"
    if not poetry:
        try:
            SpecifierSet(new)
        except InvalidSpecifier:
            return conflict(f"result {new!r} is not a valid specifier")
    return SpecBump(new, "ok" if new == spec else "bumped")


# --------------------------------------------------------------------------
# files

_CLAUSE = r"(?:===|==|!=|~=|>=|<=|>|<)\s*[\w.*+!-]+"
_SPEC = rf"\s*{_CLAUSE}(?:\s*,\s*{_CLAUSE})*"


def _req_regex(package: str) -> re.Pattern:
    name = re.escape(package).replace(r"\-", "[-_.]").replace("_", "[-_.]")
    return re.compile(
        rf"(?P<req>(?P<name>{name})(?P<extras>\s*\[[^\]]*\])?(?P<spec>{_SPEC})?)(?=\s*(?:;|#|$))",
        re.I,
    )


# A comment on the pin's line, or a comment line right above it, that says the
# pin is deliberate. Such pins are never bumped.
INTENT_RE = re.compile(
    r"\b(on purpose|intentional(?:ly)?|pinned|keep|"
    r"do\s*n[o']?t\s+(?:upgrade|bump|update|change)|do not (?:upgrade|bump|update|change))\b",
    re.I,
)


def comment_of(line: str) -> str:
    """The ``# ...`` part of a line, ignoring ``#`` inside quotes."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[i:].strip()
    return ""


def intent_comment(line: str, previous: str | None) -> str | None:
    own = comment_of(line)
    if own and INTENT_RE.search(own):
        return own
    if previous is not None and previous.lstrip().startswith("#") and INTENT_RE.search(previous):
        return previous.strip()
    return None


def _is_requirements_file(rel: Path) -> bool:
    name = rel.name.lower()
    if rel.suffix in (".txt", ".in") and (
        name.startswith(("requirements", "constraints")) or "requirements" in rel.parts[:-1]
    ):
        return True
    return False


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            yield (Path(dirpath) / name).relative_to(root)


class _Planner:
    def __init__(self, root: Path, package: str, minimum: Version):
        self.root = root
        self.package = package
        self.req = _req_regex(package)
        self.plan = PinPlan(minimum=minimum)
        self._previous: str | None = None  # line above the one being handled

    # Each handler returns the (possibly rewritten) line.
    def _bump_req(self, rel, lineno, line, start, end, spec, req_text, poetry=False):
        result = bump_spec(spec, self.plan.minimum, poetry=poetry)
        if result.status == "conflict":
            self.plan.conflicts.append(PinConflict(rel, lineno, req_text.strip(), result.reason))
            return line
        if result.status != "bumped":
            return line
        if why := intent_comment(line, self._previous):
            self.plan.holds.append(PinHold(rel, lineno, req_text.strip(), why))
            return line
        new_line = line[:start] + result.new + line[end:]
        new_req = req_text.replace(spec, result.new, 1) if spec in req_text else req_text
        self.plan.edits.append(PinEdit(rel, lineno, req_text.strip(), new_req.strip()))
        return new_line

    def _requirement_in(self, rel, lineno, line, text_start, text):
        """``text`` is a requirement string located at ``line[text_start:]``."""
        m = self.req.match(text)
        if not m or not m.group("spec"):
            return line
        spec = m.group("spec")
        lead_ws = len(spec) - len(spec.lstrip())
        s = text_start + m.start("spec") + lead_ws
        return self._bump_req(rel, lineno, line, s, text_start + m.end("spec"), spec.lstrip(), m.group("req"))

    def requirements_line(self, rel, lineno, line, allow_key=False):
        if line.lstrip().startswith(("#", "-")):
            return line
        lead = re.match(r"\s*(?:[\w-]+\s*=\s*)?" if allow_key else r"\s*", line).end()
        return self._requirement_in(rel, lineno, line, lead, line[lead:])

    def quoted_strings(self, rel, lineno, line):
        for q in reversed(list(re.finditer(r"([\"'])([^\"'\n]*)\1", line))):
            inner_start = q.start(2)
            inner = q.group(2)
            lead = len(inner) - len(inner.lstrip())
            stripped = inner.strip()
            m = self.req.match(stripped)
            if m and m.end() == len(stripped.split(";")[0].rstrip()):
                line = self._requirement_in(rel, lineno, line, inner_start + lead, inner[lead:])
        return line

    def poetry_line(self, rel, lineno, line):
        name = re.escape(self.package)
        m = re.match(rf"\s*{name}\s*=\s*([\"'])(?P<spec>[^\"']*)\1", line, re.I) or re.match(
            rf"\s*{name}\s*=\s*\{{[^}}]*?\bversion\s*=\s*([\"'])(?P<spec>[^\"']*)\1", line, re.I
        )
        if not m:
            return line
        spec = m.group("spec")
        return self._bump_req(rel, lineno, line, m.start("spec"), m.end("spec"), spec, line.strip(), poetry=True)

    def file(self, rel: Path):
        path = self.root / rel
        name = rel.name
        if _is_requirements_file(rel):
            handlers = [lambda r, n, l: self.requirements_line(r, n, l)]
        elif name == "setup.cfg":
            handlers = [lambda r, n, l: self.requirements_line(r, n, l, allow_key=True)]
        elif name == "setup.py":
            handlers = [self.quoted_strings]
        elif name == "pyproject.toml":
            handlers = [self.quoted_strings, self.poetry_line]
        elif name in ("poetry.lock", "uv.lock", "pdm.lock", "Pipfile.lock"):
            self.lock(rel, path.read_text(errors="replace"))
            return
        else:
            return
        text = path.read_text(errors="replace")
        if self.package.lower() not in text.lower():
            return
        lines = text.splitlines(keepends=True)
        changed = False
        for i, raw in enumerate(lines):
            body = raw.rstrip("\r\n")
            nl = raw[len(body):]
            new = body
            self._previous = lines[i - 1].rstrip("\r\n") if i else None
            for handler in handlers:
                new = handler(rel, i + 1, new)
            if new != body:
                lines[i] = new + nl
                changed = True
        if changed:
            self.plan.files[rel] = "".join(lines)

    def lock(self, rel: Path, text: str):
        version = None
        if rel.name == "Pipfile.lock":
            try:
                data = json.loads(text)
            except ValueError:
                return
            for section in ("default", "develop"):
                entry = data.get(section, {}).get(self.package)
                if entry and "version" in entry:
                    version = entry["version"].lstrip("=")
                    break
        else:
            m = re.search(rf'name\s*=\s*"{re.escape(self.package)}"\s*\n\s*version\s*=\s*"([^"]+)"', text, re.I)
            version = m.group(1) if m else None
        v = _version(version) if version else None
        if v is not None and v < self.plan.minimum:
            self.plan.lock_notes.append(LockNote(rel, version))


def plan_pins(root: Path, package: str, minimum: str | Version) -> PinPlan:
    planner = _Planner(Path(root), package, Version(str(minimum)))
    for rel in _walk(Path(root)):
        planner.file(rel)
    return planner.plan


def apply_pins(root: Path, plan: PinPlan) -> None:
    for rel, text in plan.files.items():
        (Path(root) / rel).write_text(text)
