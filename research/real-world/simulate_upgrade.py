"""Rewrite every openai pin in a clone to 3.36.1 and commit it on a local branch.

    python simulate_upgrade.py CLONE_DIR
Keeps each file's style: `openai==1.2` -> `openai==3.36.1`, `openai<3` ->
`openai==3.36.1`, Poetry `"^1.58"` -> `"^3.36.1"`, bare Poetry `"1.1.1"` ->
`"3.36.1"`. Nothing is pushed.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from api_change_agent.deps import declarations  # noqa: E402

TARGET = "3.36.1"
BRANCH = "aca-sim/openai-3.36.1"
repo = Path(sys.argv[1]).resolve()
changed = []
for d in declarations(repo, "openai"):
    path = repo / d.path
    lines = path.read_text().splitlines(keepends=True)
    line = lines[d.line - 1]
    if d.kind == "poetry":
        new_spec = f"^{TARGET}" if d.spec.startswith("^") else TARGET
    else:
        new_spec = f"=={TARGET}"
    if d.spec:
        start = line.index(d.spec, line.lower().index("openai"))
        lines[d.line - 1] = line[:start] + new_spec + line[start + len(d.spec):]
    elif d.kind != "poetry":
        start = line.lower().index("openai") + len("openai")
        lines[d.line - 1] = line[:start] + new_spec + line[start:]
    if lines[d.line - 1] != line:
        path.write_text("".join(lines))
        changed.append(f"{d.path}:{d.line}: {line.strip()}  ->  {lines[d.line - 1].strip()}")
git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True)
git("checkout", "-q", "-B", BRANCH)
git("add", "-A")
git("-c", "user.name=aca-sim", "-c", "user.email=aca-sim@localhost", "commit", "-q", "-m", f"Simulate upgrade to openai {TARGET}")
print("\n".join(changed) or "no openai pins found")
