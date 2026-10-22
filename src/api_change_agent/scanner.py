"""Find Python files that import the target package."""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path

import libcst as cst

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", ".env", ".tox", ".nox",
    "node_modules", "__pycache__", "site-packages", "build", "dist",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".eggs",
}


@dataclass
class ScanResult:
    files: list[Path] = field(default_factory=list)  # repo-relative
    unparsable: list[Path] = field(default_factory=list)


def _module_root(node: cst.BaseExpression | None) -> str | None:
    while isinstance(node, cst.Attribute):
        node = node.value
    return node.value if isinstance(node, cst.Name) else None


class _ImportFinder(cst.CSTVisitor):
    def __init__(self, package: str):
        self.package = package
        self.found = False

    def visit_Import(self, node: cst.Import) -> bool:
        if any(_module_root(a.name) == self.package for a in node.names):
            self.found = True
        return False

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        if not node.relative and _module_root(node.module) == self.package:
            self.found = True
        return False


def imports_package(source: str | cst.Module, package: str) -> bool:
    if isinstance(source, str):
        if package not in source:
            return False
        source = cst.parse_module(source)
    finder = _ImportFinder(package)
    source.visit(finder)
    return finder.found


def iter_python_files(root: Path, exclude: list[str] | tuple[str, ...] = ()):
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")
        )
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            rel = (Path(dirpath) / name).relative_to(root)
            if any(fnmatch.fnmatch(rel.as_posix(), pat) for pat in exclude):
                continue
            yield rel


def scan_repo(root: Path, package: str, exclude=()) -> ScanResult:
    result = ScanResult()
    for rel in iter_python_files(root, exclude):
        try:
            text = (Path(root) / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if package not in text:
            continue
        try:
            if imports_package(cst.parse_module(text), package):
                result.files.append(rel)
        except cst.ParserSyntaxError:
            result.unparsable.append(rel)
    return result
