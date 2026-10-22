"""Install the repo's dependencies and run its tests."""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
import venv
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

SESSION_ERROR = "<session>"
# Env vars that look like credentials never reach the repo's install or tests.
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH", re.I)
_PYTEST = re.compile(r"(^|[\s/])(pytest|py\.test)(\s|$)")


@dataclass(frozen=True)
class TestOutcome:
    __test__ = False  # not a pytest test class

    command: str
    passed: bool
    returncode: int
    failures: frozenset[str] = field(default_factory=frozenset)
    has_junit: bool = False
    output: str = ""

    def summary(self) -> str:
        if self.passed:
            return "passed"
        if self.failures:
            return f"{len(self.failures)} failing"
        return f"failed (exit {self.returncode})"


def new_failures(baseline: TestOutcome | None, after: TestOutcome | None) -> set[str]:
    if after is None or after.passed:
        return set()
    if baseline is None:
        return set(after.failures) or {f"<exit {after.returncode}>"}
    if baseline.has_junit and after.has_junit:
        return set(after.failures - baseline.failures)
    return set() if not baseline.passed else (set(after.failures) or {f"<exit {after.returncode}>"})


def detect_test_command(repo: Path) -> str | None:
    repo = Path(repo)
    markers = [repo / "pytest.ini", repo / "conftest.py", repo / "tests", repo / "test"]
    if any(p.exists() for p in markers):
        return "python -m pytest -q"
    for cfg, needle in (("pyproject.toml", "[tool.pytest"), ("setup.cfg", "[tool:pytest]"), ("tox.ini", "[pytest]")):
        p = repo / cfg
        if p.exists() and needle in p.read_text(errors="replace"):
            return "python -m pytest -q"
    if any(repo.glob("test_*.py")):
        return "python -m pytest -q"
    return None


def default_install_command(repo: Path, test_command: str | None) -> str:
    repo = Path(repo)
    steps = ["python -m pip install -q --upgrade pip"]
    for name in ("requirements.txt", "requirements-dev.txt", "dev-requirements.txt", "requirements-test.txt"):
        if (repo / name).exists():
            steps.append(f"python -m pip install -q -r {name}")
    pyproject = repo / "pyproject.toml"
    installable = pyproject.exists() and any(
        h in pyproject.read_text(errors="replace") for h in ("[project]", "[build-system]", "[tool.poetry]")
    )
    if installable or (repo / "setup.py").exists():
        steps.append("python -m pip install -q -e .")
    if test_command and _PYTEST.search(test_command):
        steps.append("python -m pip install -q pytest")
    return " && ".join(steps)


class TestRunner:
    __test__ = False  # not a pytest test class

    def __init__(self, repo: Path, command: str, install_command: str | None = None,
                 venv_dir: Path | None = None, timeout: int = 1800, log=print,
                 venv_python: str | None = None):
        self.repo = Path(repo)
        self.command = command
        self.install_command = install_command
        self.venv_dir = Path(venv_dir) if venv_dir else None
        self.venv_python = venv_python
        self.timeout = timeout
        self.log = log

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.venv_dir:
            bindir = self.venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
            env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
            env["VIRTUAL_ENV"] = str(self.venv_dir)
            env.pop("PYTHONHOME", None)
        # Never let repo code reach a real API with our credentials.
        for name in [n for n in env if SECRET_NAME.search(n)]:
            del env[name]
        return env

    def _sh(self, command: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            command, shell=True, cwd=self.repo, env=self._env(), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=self.timeout,
        )

    def setup(self) -> None:
        if self.venv_dir and not (self.venv_dir / "bin").exists():
            if self.venv_python:
                subprocess.run([self.venv_python, "-m", "venv", str(self.venv_dir)], check=True)
            else:
                venv.create(self.venv_dir, with_pip=True)
        self.install()

    def install(self) -> None:
        if not self.install_command:
            return
        self.log(f"install: {self.install_command}")
        proc = self._sh(self.install_command)
        if proc.returncode != 0:
            raise RuntimeError(f"install command failed (exit {proc.returncode}):\n{proc.stdout[-4000:]}")

    def run(self) -> TestOutcome:
        command = self.command
        junit = None
        if _PYTEST.search(command) and not re.search(r"[;&|]", command):
            fd, junit = tempfile.mkstemp(suffix=".xml", prefix="aca-junit-")
            os.close(fd)
            os.unlink(junit)
            command = f"{command} --junitxml={shlex.quote(junit)}"
        self.log(f"test: {self.command}")
        try:
            proc = self._sh(command)
            rc, out = proc.returncode, proc.stdout
        except subprocess.TimeoutExpired as e:
            rc, out = -1, f"timed out after {self.timeout}s\n{e.stdout or ''}"
        failures: set[str] = set()
        has_junit = False
        if junit and os.path.exists(junit):
            try:
                failures = _junit_failures(junit)
                has_junit = True
            except ET.ParseError:
                pass
            finally:
                os.unlink(junit)
        passed = rc in (0, 5)  # 5 = pytest collected nothing
        if not passed and has_junit and not failures:
            failures = {SESSION_ERROR}  # e.g. collection/import error
        return TestOutcome(self.command, passed, rc, frozenset(failures), has_junit, out[-6000:])


def _junit_failures(path: str) -> set[str]:
    failed = set()
    for case in ET.parse(path).getroot().iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            failed.add(f"{case.get('classname', '')}::{case.get('name', '')}")
    return failed
