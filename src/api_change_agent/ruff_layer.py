"""Run ruff as a deterministic first pass and account for every change it makes.

ruff's JSON output says, per diagnostic, which edits its fix makes (in the
original file's coordinates). We diagnose, apply --fix, diagnose again, then
credit each changed hunk to the diagnostics whose edits touched it. Anything
still reported after --fix is left for a human.
"""
from __future__ import annotations

import difflib
import json
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .rules import Tool


@dataclass(frozen=True)
class Diagnostic:
    path: str
    line: int
    code: str
    message: str
    url: str
    edit_lines: tuple[tuple[int, int], ...]  # (start, end) rows of the fix's edits; empty = no fix


@dataclass(frozen=True)
class RuffChange:
    path: str
    line: int  # in the fixed file
    before: str
    after: str
    codes: tuple[str, ...]
    messages: tuple[str, ...]
    url: str


@dataclass
class RuffResult:
    changes: list[RuffChange] = field(default_factory=list)
    left: list[Diagnostic] = field(default_factory=list)
    diagnosed: list[Diagnostic] = field(default_factory=list)
    new_text: dict[str, str] = field(default_factory=dict)


def _cmd(tool: Tool, *extra: str) -> list[str]:
    return [sys.executable, "-m", "ruff", "check", "--no-cache", "--exit-zero", "--select", tool.select,
            *tool.args, *extra]


def diagnose(repo: Path, files: list[str], tool: Tool) -> list[Diagnostic]:
    if not files:
        return []
    proc = subprocess.run(_cmd(tool, "--output-format", "json", *files), cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ruff failed: {proc.stderr.strip()[:500]}")
    out = []
    for d in json.loads(proc.stdout or "[]"):
        edits = tuple((e["location"]["row"], e["end_location"]["row"]) for e in ((d.get("fix") or {}).get("edits") or ()))
        out.append(Diagnostic(Path(d["filename"]).resolve().relative_to(repo.resolve()).as_posix(),
                              d["location"]["row"], d.get("code") or "", d.get("message", ""), d.get("url") or "", edits))
    return out


def run(repo: Path, files: list[str], tool: Tool) -> RuffResult:
    repo = Path(repo)
    res = RuffResult(diagnosed=diagnose(repo, files, tool))
    touched = sorted({d.path for d in res.diagnosed if d.edit_lines})
    if not res.diagnosed:
        return res
    before = {f: (repo / f).read_text() for f in touched}
    if touched:
        unused_before = {(d["filename"], d["message"]) for d in _unused_imports(repo, touched)}
        proc = subprocess.run(_cmd(tool, "--fix", *touched), cwd=repo, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ruff --fix failed: {proc.stderr.strip()[:500]}")
        _drop_imports_left_unused(repo, touched, unused_before)
    res.left = diagnose(repo, files, tool)
    remaining = Counter((d.path, d.code, d.message) for d in res.left)
    fixed = []
    for d in res.diagnosed:
        if remaining[(d.path, d.code, d.message)] > 0:
            remaining[(d.path, d.code, d.message)] -= 1
        elif d.edit_lines:
            fixed.append(d)
    for f in touched:
        after = (repo / f).read_text()
        res.new_text[f] = after
        res.changes += _attribute(f, before[f], after, [d for d in fixed if d.path == f])
    return res


def _unused_imports(repo: Path, files: list[str]) -> list[dict]:
    proc = subprocess.run([sys.executable, "-m", "ruff", "check", "--no-cache", "--exit-zero", "--isolated",
                           "--select", "F401", "--output-format", "json", *files], cwd=repo, capture_output=True, text=True)
    return json.loads(proc.stdout or "[]") if proc.returncode == 0 else []


def _drop_imports_left_unused(repo: Path, files: list[str], unused_before: set) -> None:
    """ruff's AIR fixes import the new name but keep the old import, which fails on Airflow 3 once
    unused. Remove exactly those: airflow imports that became unused, with ruff's own F401 edits."""
    by_file: dict[str, list[dict]] = {}
    for d in _unused_imports(repo, files):
        name = d["message"].split("`")[1] if "`" in d["message"] else ""
        if name.startswith("airflow.") and (d["filename"], d["message"]) not in unused_before and d.get("fix"):
            by_file.setdefault(d["filename"], []).extend(d["fix"]["edits"])
    for filename, edits in by_file.items():
        lines = Path(filename).read_text().splitlines(keepends=True)
        starts = [0]
        for ln in lines:
            starts.append(starts[-1] + len(ln))
        text = "".join(lines)
        offset = lambda loc: starts[loc["row"] - 1] + loc["column"] - 1  # noqa: E731
        limit = len(text) + 1
        for e in sorted({(offset(e["location"]), offset(e["end_location"]), e.get("content") or "") for e in edits},
                        reverse=True):
            if e[1] > limit:
                continue  # overlaps an edit already applied (two names from one import): that one covers it
            text, limit = text[:e[0]] + e[2] + text[e[1]:], e[0]
        Path(filename).write_text(text)


def _attribute(path: str, old: str, new: str, fixed: list[Diagnostic]) -> list[RuffChange]:
    a, b = old.splitlines(), new.splitlines()
    changes = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        lo, hi = i1 + 1, max(i2, i1 + 1)  # 1-based old rows this hunk covers (inserts: the row after)
        owners = [d for d in fixed if any(s <= hi and e >= lo - 1 for s, e in d.edit_lines)]
        if not owners and fixed:  # e.g. an import ruff added elsewhere for a fix: credit the closest one
            owners = [min(fixed, key=lambda d: min(abs(s - lo) for s, _ in d.edit_lines))]
        owners = sorted(set(owners), key=lambda d: (d.line, d.code))
        changes.append(RuffChange(
            path, j1 + 1, "\n".join(a[i1:i2]), "\n".join(b[j1:j2]),
            tuple(dict.fromkeys(d.code for d in owners)), tuple(dict.fromkeys(d.message for d in owners)),
            owners[0].url if owners else "",
        ))
    return changes
