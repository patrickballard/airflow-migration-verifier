"""Find public repos to try the agent on (read-only; uses `gh`).

Keeps repos that import openai, ideally hit one of the agent's patterns,
pin openai below 3.0, and have test files. Skips forks, archived repos,
copies of the SDK itself, and repos over MAX_KB / MAX_FILES.

    python find_candidates.py > candidates.json
"""
import base64
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from packaging.specifiers import SpecifierSet  # noqa: E402

from api_change_agent.deps import declarations, spec_allows  # noqa: E402

QUERIES = {
    "beta_parse": '"beta.chat.completions.parse" language:python',
    "beta_stream": '"beta.chat.completions.stream" language:python',
    "functions": '"function_call=" "functions=" "from openai import" language:python',
    "httpx": '"import httpx" "from openai import OpenAI" language:python',
}
VENDORED = re.compile(r"(site-packages|dist-packages|(^|/)(\.?venv|env|lib/python[\d.]+)/"
                      r"|(^|/)openai/(types|resources|_[a-z_]+|lib|cli)(/|\.py))")
TEST_FILE = re.compile(r"(^|/)(tests?/.*\.py|test_[^/]*\.py|[^/]*_test\.py)$")
DEP_FILES = ("requirements.txt", "requirements-dev.txt", "requirements/base.txt", "pyproject.toml",
             "setup.py", "setup.cfg")
MAX_KB, MAX_FILES, WANT = 30_000, 1500, 25
V3 = SpecifierSet(">=3.0.0")


def gh(*args):
    r = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


def log(*a):
    print(*a, file=sys.stderr)


hits: dict[str, set[str]] = {}
for tag, q in QUERIES.items():
    for page in (1, 2):
        d = gh("-X", "GET", "search/code", "-f", f"q={q}", "-f", "per_page=100", "-f", f"page={page}")
        for item in (d or {}).get("items", []):
            name = item["repository"]["full_name"]
            if VENDORED.search(item["path"]) or "openai-python" in name:
                continue
            hits.setdefault(name, set()).add(tag)
        time.sleep(7)  # code search: ~10 requests/minute
log(f"{len(hits)} repos from search")

# patterns that matter most first: beta / functions, then httpx-only
order = sorted(hits, key=lambda r: (-len(hits[r] - {"httpx"}), -len(hits[r]), r))
kept = []
for name in order:
    if len(kept) >= WANT:
        break
    meta = gh(f"repos/{name}")
    if not meta or meta["fork"] or meta["archived"] or meta["size"] > MAX_KB:
        continue
    tree = gh(f"repos/{name}/git/trees/{meta['default_branch']}?recursive=1")
    if not tree or tree.get("truncated"):
        continue
    paths = [t["path"] for t in tree["tree"] if t["type"] == "blob"]
    tests = [p for p in paths if TEST_FILE.search(p) and not VENDORED.search(p)]
    if len(paths) > MAX_FILES or len(tests) < 2:
        continue
    with tempfile.TemporaryDirectory() as tmp:
        for f in DEP_FILES:
            if f in paths:
                blob = gh(f"repos/{name}/contents/{f}")
                if blob and blob.get("content"):
                    (Path(tmp) / f).parent.mkdir(parents=True, exist_ok=True)
                    (Path(tmp) / f).write_text(base64.b64decode(blob["content"]).decode("utf-8", "replace"))
        decls = declarations(Path(tmp), "openai")
    pins = [(str(d.path), d.line, d.text) for d in decls]
    if not decls or any(spec_allows(d.spec, V3, poetry=d.kind == "poetry") for d in decls):
        continue  # unpinned / allows 3.x / no declaration we can read
    kept.append({"repo": name, "patterns": sorted(hits[name]), "pins": pins, "tests": len(tests),
                 "files": len(paths), "size_kb": meta["size"], "stars": meta["stargazers_count"],
                 "pushed_at": meta["pushed_at"], "default_branch": meta["default_branch"]})
    log(f"  kept {name} {sorted(hits[name])} {pins} tests={len(tests)}")
json.dump(kept, sys.stdout, indent=2)
