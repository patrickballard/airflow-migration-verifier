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
DEV_GROUPS = ("dev", "test", "tests", "testing")
# Lets modules that build an OpenAI() client at import time load in tests;
# a real request with it just gets a 401.
DUMMY_OPENAI_KEY = "sk-api-change-agent-dummy-key"
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
    executed: int | None = None  # tests that actually ran (from junit), None if unknown
    ids: tuple[str, ...] = ()  # every testcase id in the junit report, passing or not
    messages: dict = field(default_factory=dict)  # failure message per failing id

    def summary(self) -> str:
        if self.executed is None:
            if self.passed:
                return "passed"
            return f"{len(self.failures)} failing" if self.failures else f"failed (exit {self.returncode})"
        # collection errors have no class name ("::module"); they never ran
        collection = sum(1 for f in self.failures if f.startswith("::"))
        failing = len(self.failures) - collection
        text = f"{self.executed - failing} passed, {failing} failing"
        return text + (f", {collection} collection error{'s' * (collection != 1)}" if collection else "")


def new_failures(baseline: TestOutcome | None, after: TestOutcome | None) -> set[str]:
    if after is None or after.passed:
        return set()
    if baseline is None:
        return set(after.failures) or {f"<exit {after.returncode}>"}
    if baseline.has_junit and after.has_junit:
        return {t for t in after.failures - baseline.failures if not _was_blocked(t, baseline)}
    return set() if not baseline.passed else (set(after.failures) or {f"<exit {after.returncode}>"})


def merge_outcome(base: TestOutcome, partial: TestOutcome, files: list[str]) -> TestOutcome:
    """`base` with the checks of `files` replaced by `partial`'s (a re-test of just those files)."""
    redone = set(files)
    keep = [i for i in base.ids if i.split("::", 1)[0] not in redone]
    failures = frozenset({f for f in base.failures if f.split("::", 1)[0] not in redone} | partial.failures)
    messages = {k: v for k, v in base.messages.items() if k.split("::", 1)[0] not in redone}
    messages.update(partial.messages)
    return TestOutcome(base.command, not failures, 1 if failures else 0, failures, True,
                       partial.output, len(keep) + len(partial.ids), tuple(keep) + tuple(partial.ids), messages)


def _was_blocked(test_id: str, baseline: TestOutcome) -> bool:
    """A check that didn't exist at baseline because something before it failed.

    Fixing an import can let a DAG (or a test module) get far enough to hit
    its *next* problem; that is progress, not a regression.
    """
    if test_id in baseline.ids:
        return False
    owner = test_id.split("::", 1)[0]
    if f"{owner}::import" in baseline.failures:  # Airflow DAG checker: the file didn't import
        return True
    for failed in baseline.failures:  # pytest collection error "::pkg.module" blocks "pkg.module[.Class]::test"
        if failed.startswith("::") and (owner == failed[2:] or owner.startswith(failed[2:] + ".")):
            return True
    return False


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


def default_install_command(repo: Path, test_command: str | None, constraints: str = "") -> str:
    """`constraints` is a pip -c URL/path; `{python}` in it becomes the venv's X.Y."""
    repo = Path(repo)
    steps = ["python -m pip install -q --upgrade pip"]
    c = ""
    if constraints:
        py = "$(python -c 'import sys; print(\"%d.%d\" % sys.version_info[:2])')"
        c = " -c \"" + constraints.replace("{python}", py) + "\""
    for name in ("requirements.txt", "requirements-dev.txt", "dev-requirements.txt", "requirements-test.txt"):
        if (repo / name).exists():
            steps.append(f"python -m pip install -q -r {name}{c}")
    pyproject = repo / "pyproject.toml"
    installable = pyproject.exists() and any(
        h in pyproject.read_text(errors="replace") for h in ("[project]", "[build-system]", "[tool.poetry]")
    )
    extras, groups, poetry_dev = _dev_dependencies(pyproject) if pyproject.exists() else ([], [], [])
    if installable or (repo / "setup.py").exists():
        target = f".[{','.join(extras)}]" if extras else "."
        steps.append(f"python -m pip install -q -e {shlex.quote(target)}{c}")
    steps += [f"python -m pip install -q --group {shlex.quote(g)}" for g in groups]
    if poetry_dev:
        steps.append("python -m pip install -q " + " ".join(shlex.quote(r) for r in poetry_dev))
    if test_command and _PYTEST.search(test_command):
        steps.append("python -m pip install -q pytest")
    return " && ".join(steps)


def _dev_dependencies(pyproject: Path) -> tuple[list[str], list[str], list[str]]:
    """Test/dev dependencies declared in pyproject.toml.

    Returns (PEP 621 extras, PEP 735 dependency groups, Poetry dev/test group
    requirements as PEP 440 strings)."""
    try:
        import tomllib
    except ImportError:  # Python 3.10
        return [], [], []
    try:
        data = tomllib.loads(pyproject.read_text(errors="replace"))
    except tomllib.TOMLDecodeError:
        return [], [], []
    extras = [k for k in (data.get("project", {}).get("optional-dependencies") or {}) if k.lower() in DEV_GROUPS]
    groups = [k for k in (data.get("dependency-groups") or {}) if k.lower() in DEV_GROUPS]
    poetry = data.get("tool", {}).get("poetry", {})
    declared = dict(poetry.get("dev-dependencies") or {})
    for name, body in (poetry.get("group") or {}).items():
        if name.lower() in DEV_GROUPS:
            declared.update(body.get("dependencies") or {})
    from .deps import _poetry_to_pep440

    reqs = []
    for name, spec in declared.items():
        if name.lower() == "python":
            continue
        if isinstance(spec, dict):
            if {"path", "git", "url"} & set(spec):
                continue
            spec = spec.get("version", "")
        try:
            reqs.append(name + (_poetry_to_pep440(spec) if isinstance(spec, str) and spec not in ("", "*") else ""))
        except (ValueError, IndexError):
            reqs.append(name)
    return extras, groups, reqs


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
        env["OPENAI_API_KEY"] = DUMMY_OPENAI_KEY
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

    def run(self, extra_args: tuple[str, ...] = ()) -> TestOutcome:
        """Run the test command; `extra_args` are appended (e.g. the DAG checker's --files)."""
        command = self.command + "".join(f" {shlex.quote(a)}" for a in extra_args)
        junit = None
        if "{junit}" in command:  # e.g. the Airflow DAG checker writes junit where we say
            fd, junit = tempfile.mkstemp(suffix=".xml", prefix="aca-junit-")
            os.close(fd)
            os.unlink(junit)
            command = command.replace("{junit}", shlex.quote(junit))
        elif _PYTEST.search(command) and not re.search(r"[;&|]", command):
            fd, junit = tempfile.mkstemp(suffix=".xml", prefix="aca-junit-")
            os.close(fd)
            os.unlink(junit)
            # One module failing to import must not stop every other test from running.
            command = f"{command} --continue-on-collection-errors --junitxml={shlex.quote(junit)}"
        self.log(f"test: {self.command}")
        try:
            proc = self._sh(command)
            rc, out = proc.returncode, proc.stdout
        except subprocess.TimeoutExpired as e:
            rc, out = -1, f"timed out after {self.timeout}s\n{e.stdout or ''}"
        failures: set[str] = set()
        has_junit = False
        executed, ids, messages = None, (), {}
        if junit and os.path.exists(junit):
            try:
                failures, executed, ids, messages = _junit_failures(junit)
                has_junit = True
            except ET.ParseError:
                pass
            finally:
                os.unlink(junit)
        passed = rc in (0, 5)  # 5 = pytest collected nothing
        if not passed and has_junit and not failures:
            failures = {SESSION_ERROR}  # e.g. collection/import error
        return TestOutcome(self.command, passed, rc, frozenset(failures), has_junit, out[-6000:], executed,
                           tuple(ids), messages)


def _junit_failures(path: str) -> tuple[set[str], int, list[str], dict[str, str]]:
    """(failed ids, number of tests that actually ran, all ids, failure message per failed id).

    Collection errors appear as testcases with an empty name or a classname
    but no test function; they count as failures, not as executed tests."""
    failed, executed, ids, messages = set(), 0, [], {}
    for case in ET.parse(path).getroot().iter("testcase"):
        test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
        ids.append(test_id)
        is_collection_error = not case.get("classname")
        problem = case.find("failure") if case.find("failure") is not None else case.find("error")
        if problem is not None:
            failed.add(test_id)
            messages[test_id] = (problem.get("message") or problem.text or "").strip().splitlines()[0][:300] \
                if (problem.get("message") or problem.text) else ""
        if case.find("skipped") is None and not is_collection_error:
            executed += 1
    return failed, executed, ids, messages
