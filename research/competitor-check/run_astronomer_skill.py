"""Astronomer's migrating-airflow-2-to-3 skill, in a fresh headless Claude Code session per repo.

    python run_astronomer_skill.py eliasbenaddou__personal_finance_de_project [...]

Each session starts in an empty temp directory holding a copy of the ORIGINAL repo (committed to a new git
repo) and the skill under .claude/skills/. It sees nothing of api-change-agent: no CLAUDE.md, no memory, no user
settings, skills or plugins (--setting-sources project), no MCP servers (--strict-mcp-config). The prompt is
plain. Tools: edits auto-accepted, Bash limited to read-only commands, ruff and py_compile; no network installs.
The result tree is copied to ~/SDK/competitor-check/trees/astro-skill/<repo> without any edit.
Auth is the logged-in Claude Code account (OAuth), not an API key.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WORK = Path.home() / "SDK" / "competitor-check"
SCAN = Path.home() / "SDK" / "real-world-airflow" / "_scan"
SKILL = WORK / "astronomer-agents" / "skills" / "migrating-airflow-2-to-3"
PROMPT = "Migrate this repo to Airflow 3."
ALLOWED = ["Read", "Edit", "Write", "Glob", "Grep", "Bash(ruff:*)", "Bash(grep:*)", "Bash(rg:*)", "Bash(find:*)",
           "Bash(ls:*)", "Bash(cat:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(wc:*)", "Bash(git status:*)",
           "Bash(git diff:*)", "Bash(git log:*)", "Bash(python -m py_compile:*)", "Bash(python3 -m py_compile:*)"]
ARGV = ["claude", "-p", PROMPT, "--setting-sources", "project", "--strict-mcp-config", "--permission-mode", "acceptEdits",
        "--allowedTools", *ALLOWED, "--output-format", "stream-json", "--verbose"]


def run(repo: str) -> dict:
    work = Path(tempfile.mkdtemp(prefix="astro-run-")) / repo
    shutil.copytree(SCAN / repo, work, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    git = ["git", "-c", "user.email=run@local", "-c", "user.name=run"]
    subprocess.run(git + ["init", "-q"], cwd=work, check=True)
    subprocess.run(git + ["add", "-A"], cwd=work, check=True)
    subprocess.run(git + ["commit", "-qm", "original"], cwd=work, check=True)
    shutil.copytree(SKILL, work / ".claude" / "skills" / SKILL.name)  # after the commit: not part of the repo
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "OPENAI_", "AWS_", "GITHUB_"))}
    env["PATH"] = f"{WORK / 'venvs' / 'ruff' / 'bin'}:{env['PATH']}"  # the skill tells it to run ruff
    logs = WORK / "logs" / "astro-skill" / repo
    if logs.exists():
        shutil.rmtree(logs)
    logs.mkdir(parents=True)
    t = time.time()
    try:
        p = subprocess.run(ARGV, cwd=work, env=env, capture_output=True, text=True, timeout=1800)
        out, code = p.stdout, p.returncode
    except subprocess.TimeoutExpired as e:
        out, code = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), "timeout"
    seconds = round(time.time() - t)
    (logs / "transcript.jsonl").write_text(out)
    events = [json.loads(ln) for ln in out.splitlines() if ln.startswith("{")]
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    shutil.rmtree(work / ".claude", ignore_errors=True)
    tree = WORK / "trees" / "astro-skill" / repo
    if tree.exists():
        shutil.rmtree(tree)
    shutil.copytree(work, tree, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    rec = {"repo": repo, "argv": ARGV, "exit": code, "seconds": seconds, "model": init.get("model"),
           "claude_code": init.get("claude_code_version"), "skills": init.get("skills"),
           "mcp_servers": init.get("mcp_servers"), "num_turns": result.get("num_turns"),
           "subtype": result.get("subtype"), "notional_cost_usd": result.get("total_cost_usd"),
           "final_message": (result.get("result") or "")[:4000]}
    (logs / "run.json").write_text(json.dumps(rec, indent=1))
    return rec


if __name__ == "__main__":
    for repo in sys.argv[1:]:
        r = run(repo)
        print(json.dumps({k: r[k] for k in ("repo", "exit", "seconds", "model", "num_turns", "subtype")}), flush=True)
