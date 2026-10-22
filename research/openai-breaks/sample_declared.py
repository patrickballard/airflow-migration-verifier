"""Of repos that import httpx next to openai, how many declare httpx?

    python sample_declared.py [N]    # default 30 repos, seeded
Samples repos from the `httpx_with_openai` query and reads their root
dependency files through the GitHub API.
"""
import base64
import json
import random
import re
import subprocess
import sys
import pathlib

from search import search

FILES = ["requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "requirements-dev.txt", "environment.yml"]
q = '"import httpx" "from openai import" language:python'
repos = sorted({i["repository"]["full_name"] for i in search(q)["items"]})
random.seed(1)
sample = random.sample(repos, min(int(sys.argv[1]) if len(sys.argv) > 1 else 30, len(repos)))
counts = {"declared": 0, "undeclared": 0, "unknown": 0}
for repo in sample:
    texts = []
    for f in FILES:
        r = subprocess.run(["gh", "api", f"repos/{repo}/contents/{f}", "--jq", ".content"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            texts.append(base64.b64decode(r.stdout).decode("utf-8", "replace").lower())
    blob = "\n".join(texts)
    if not texts or "openai" not in blob:
        tag = "unknown"
    elif re.search(r"(^|[\"'\s\[,])httpx(\b|\[)", blob):
        tag = "declared"
    else:
        tag = "undeclared"
    counts[tag] += 1
    print(f"  {repo:55} {tag}")
known = counts["declared"] + counts["undeclared"]
print(counts, f"undeclared share of known: {counts['undeclared'] / max(1, known):.0%}")
(pathlib.Path(__file__).parent / "results" / "httpx_declared_sample.json").write_text(json.dumps(counts) + "\n")
