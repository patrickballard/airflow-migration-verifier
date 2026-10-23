"""Where a repo sets Airflow's [core] default_timezone, the zone a naive start_date is scheduled in.

Looked for in airflow.cfg ([core] default_timezone) and in AIRFLOW__CORE__DEFAULT_TIMEZONE
assignments in .env files, shell scripts, docker-compose files, Dockerfiles and Helm values
(including the official chart's `config: core: default_timezone:`). As in Airflow, the
environment variable wins over airflow.cfg.
"""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

ENV = "AIRFLOW__CORE__DEFAULT_TIMEZONE"
SKIP = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", "site-packages", ".tox"}
ENV_FILES = (".env", ".env.*", "*.env", "*.sh", "Dockerfile*", "*.dockerfile", "*.yaml", "*.yml")
VALUE = r"""["']?([A-Za-z][A-Za-z0-9_+\-/]*)["']?\s*(?:#.*)?$"""
ENV_LINE = re.compile(rf"(?:^|[\s\-])(?:export\s+|ENV\s+)?{ENV}\s*(?:[:=]\s*|\s+){VALUE}")
HELM_NAME = re.compile(rf"""^\s*-?\s*name:\s*["']?{ENV}["']?\s*$""")
HELM_VALUE = re.compile(rf"^\s*value:\s*{VALUE}")
CFG_KEY = re.compile(rf"^\s*default_timezone\s*=\s*{VALUE}")


@dataclass(frozen=True)
class TzSource:
    path: str
    line: int
    value: str
    kind: str  # "env" or "cfg"


@dataclass
class TzFinding:
    tz: str | None                 # the zone to use; None = not set (Airflow's default, UTC) or unusable
    sources: list[TzSource] = field(default_factory=list)
    problem: str = ""              # why the setting can't be used, if it can't

    def where(self) -> str:
        return ", ".join(f"`{s.path}:{s.line}`" for s in self.sources)


def _normalise(value: str) -> str | None:
    if value.lower() == "utc":
        return "UTC"
    if value.lower() == "system":  # the scheduler host's local zone: unknowable from the repo
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None
    return value


def _env_sources(rel: str, text: str) -> list[TzSource]:
    lines = text.splitlines()
    out = []
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        if m := ENV_LINE.search(line):
            out.append(TzSource(rel, i + 1, m.group(1), "env"))
        elif HELM_NAME.match(line):
            nxt = next((j for j in range(i + 1, len(lines)) if lines[j].strip()), None)
            if nxt is not None and (m := HELM_VALUE.match(lines[nxt])):
                out.append(TzSource(rel, nxt + 1, m.group(1), "env"))
    return out


def _helm_config(rel: str, text: str) -> list[TzSource]:
    """The official chart's `config: {core: {default_timezone: X}}`."""
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        return []
    out = []
    for doc in docs:
        core = (doc.get("config") or {}).get("core") if isinstance(doc, dict) else None
        if isinstance(core, dict) and isinstance(core.get("default_timezone"), str):
            line = next((i + 1 for i, ln in enumerate(text.splitlines()) if re.match(r"\s*default_timezone\s*:", ln)), 0)
            out.append(TzSource(rel, line, core["default_timezone"], "cfg"))
    return out


def _cfg_sources(rel: str, text: str) -> list[TzSource]:
    out, section = [], None
    for i, line in enumerate(text.splitlines()):
        if m := re.match(r"^\s*\[([^\]]+)\]", line):
            section = m.group(1).strip().lower()
        elif section == "core" and (m := CFG_KEY.match(line)):
            out.append(TzSource(rel, i + 1, m.group(1), "cfg"))
    return out


def find_default_timezone(repo: Path) -> TzFinding:
    sources: list[TzSource] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(repo).as_posix()
            is_cfg = name == "airflow.cfg" or name.endswith(".cfg") and "airflow" in name
            if not is_cfg and not any(fnmatch.fnmatch(name, g) for g in ENV_FILES):
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            if is_cfg:
                sources += _cfg_sources(rel, text)
                continue
            if ENV in text:
                sources += _env_sources(rel, text)
            if name.endswith((".yaml", ".yml")) and "default_timezone" in text:
                sources += _helm_config(rel, text)
    if not sources:
        return TzFinding(None)
    used = [s for s in sources if s.kind == "env"] or sources  # the environment variable wins, as in Airflow
    bad = sorted({s.value for s in used if _normalise(s.value) is None})
    if bad:
        return TzFinding(None, used, f"`default_timezone` is set to {', '.join(f'`{b}`' for b in bad)} "
                                     f"({', '.join(f'`{s.path}:{s.line}`' for s in used)}), which isn't a fixed zone "
                                     "this agent can pin; set `timezone=` on the pin by hand")
    zones = sorted({_normalise(s.value) for s in used})
    if len(zones) > 1:
        return TzFinding(None, used, f"`default_timezone` has conflicting values {', '.join(zones)} "
                                     f"({', '.join(f'`{s.path}:{s.line}`' for s in used)}); pin "
                                     "`CronDataIntervalTimetable(cron, timezone=...)` with the deployed one by hand")
    return TzFinding(zones[0], used)
