"""Find public repos with Airflow 2 DAGs to try the agent on (read-only: `gh` search + shallow clones).

Keeps repos that pin apache-airflow 2.x or use obvious 2.x patterns, and ranks
them by variety: cron schedules, Jinja templates, context["execution_date"],
.sql/.sh template files, DAG integrity tests. Skips forks, archived repos,
repos already on Airflow 3, and repos whose DAGs are only Airflow's own
example/tutorial files copied verbatim.

    python find_candidates.py > candidates.json   # clones into ~/SDK/real-world-airflow/_scan
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

SCAN = Path.home() / "SDK" / "real-world-airflow" / "_scan"
QUERIES = {
    "execution_date": '"schedule_interval" "execution_date" path:dags language:python',
    "templates": '"schedule_interval" "{{ ds }}" path:dags language:python',
    "template_files": '"template_searchpath" "schedule_interval" language:python',
    "sh_files": '"bash_command=" ".sh" "schedule_interval" path:dags language:python',
    "pin": '"apache-airflow==2." path:requirements.txt',
    "integrity": '"DagBag" "import_errors" path:tests language:python',
}
MAX_KB, WANT_CLONES = 60_000, 45
EXAMPLES = re.compile(r"^(example_|tutorial)")  # Airflow's own example DAG names
AF2_PIN = re.compile(r"apache[-_]airflow(?:\[[^\]]*\])?\s*(?:==|~=|>=|<)\s*2\.", re.I)
AF2_IMAGE = re.compile(r"apache/airflow:(?:\$\{[^}]*:-)?2\.")
AF3 = re.compile(r"(from airflow\.sdk import|apache[-_]airflow(?:\[[^\]]*\])?\s*(?:==|>=|~=)\s*3\.|apache/airflow:3\.)")


def gh(*args):
    r = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


def log(*a):
    print(*a, file=sys.stderr)


def features(root: Path) -> dict:
    py = [p for p in root.rglob("*.py") if ".git" not in p.parts]
    texts = {p: p.read_text(errors="replace") for p in py}
    dags = [p for p, t in texts.items() if re.search(r"^\s*(from|import) airflow", t, re.M)
            and re.search(r"\bDAG\(|@dag\b", t) and "test" not in p.relative_to(root).parts[0].lower()]
    other = [p.read_text(errors="replace") for p in root.rglob("*")
             if p.is_file() and p.suffix in (".txt", ".toml", ".cfg", ".yaml", ".yml", "") and ".git" not in p.parts
             and p.stat().st_size < 200_000 and (p.name.startswith(("requirements", "pyproject", "setup", "Dockerfile",
                                                                    "docker-compose", "compose")))]
    alltext = "\n".join([*texts.values(), *other])
    dag_text = "\n".join(texts[p] for p in dags)
    return {
        "dag_files": len(dags),
        "only_examples": bool(dags) and all(EXAMPLES.match(p.name) for p in dags),
        "af2_pin": bool(AF2_PIN.search(alltext) or AF2_IMAGE.search(alltext)),
        "af2_patterns": bool(re.search(r"schedule_interval\s*=|airflow\.operators\.(bash|python)_operator|"
                                       r"execution_date|from airflow\.operators\.(bash|python|dummy) import", dag_text)),
        "af3": bool(AF3.search(alltext)),
        "cron": bool(re.search(r"""schedule(?:_interval)?\s*=\s*["'](@(hourly|daily|weekly|monthly|yearly)|[\d*/,\- ]{9,})""",
                               dag_text)),
        "jinja": "{{" in dag_text,
        "execution_date": "execution_date" in dag_text or "next_ds" in dag_text or "prev_ds" in dag_text,
        "template_files": any(p.suffix in (".sql", ".sh") for p in root.rglob("*") if ".git" not in p.parts)
                          and bool(re.search(r"""["'][\w/.\-]+\.(sql|sh)["']""", dag_text)),
        "integrity_test": bool(re.search(r"DagBag\(", "\n".join(t for p, t in texts.items() if "test" in str(p).lower()))),
    }


def score(f: dict) -> int:
    return sum(f[k] for k in ("cron", "jinja", "execution_date", "template_files", "integrity_test")) + f["af2_pin"]


def main():
    hits: dict[str, set[str]] = {}
    for tag, q in QUERIES.items():
        d = gh("-X", "GET", "search/code", "-f", f"q={q}", "-f", "per_page=100")
        for item in (d or {}).get("items", []):
            hits.setdefault(item["repository"]["full_name"], set()).add(tag)
        log(f"{tag}: {len((d or {}).get('items', []))}")
        time.sleep(7)  # code search: ~10 requests/minute
    order = sorted(hits, key=lambda r: (-len(hits[r]), r))
    log(f"{len(hits)} repos from search")
    SCAN.mkdir(parents=True, exist_ok=True)
    out, cloned = [], 0
    for name in order:
        if cloned >= WANT_CLONES:
            break
        if "apache/airflow" == name.lower() or name.lower().startswith("apache/"):
            continue
        meta = gh(f"repos/{name}")
        if not meta or meta["fork"] or meta["archived"] or meta["size"] > MAX_KB:
            continue
        dest = SCAN / name.replace("/", "__")
        if not dest.exists():
            r = subprocess.run(["git", "clone", "-q", "--depth", "1", meta["clone_url"], str(dest)],
                               capture_output=True, text=True, timeout=300)
            if r.returncode:
                continue
        cloned += 1
        f = features(dest)
        f.update(repo=name, stars=meta["stargazers_count"], pushed=meta["pushed_at"][:10],
                 sha=subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True,
                                    text=True).stdout.strip(), search=sorted(hits[name]), score=score(f))
        f["eligible"] = (f["dag_files"] >= 1 and not f["af3"] and not f["only_examples"]
                         and (f["af2_pin"] or f["af2_patterns"]))
        out.append(f)
        log(f"{name}: dags={f['dag_files']} score={f['score']} eligible={f['eligible']}")
    out.sort(key=lambda f: (-f["eligible"], -f["score"], -f["stars"]))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
