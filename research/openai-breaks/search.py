"""Estimate public usage of each broken pattern with GitHub code search.

    python search.py            # all queries in search/queries.tsv
Needs an authenticated `gh`. Code search allows ~10 requests/min, so this
sleeps between queries. Writes results/counts.tsv and results/estimates.json.

The count is matching *files* (default branches only, approximate). Repos are
estimated as total * (distinct non-vendored repos in the first 100 hits / 100),
dropping hits that are copies of the SDK itself (forks of openai-python,
committed site-packages, vendored openai/ packages).
"""
import json
import pathlib
import re
import subprocess
import time

HERE = pathlib.Path(__file__).parent
VENDORED = re.compile(r"(site-packages|dist-packages|(^|/)(\.?venv|env|lib/python[\d.]+)/"
                      r"|(^|/)openai/(types|resources|_[a-z_]+|lib|cli)(/|\.py)|(^|/)(src/)?openai/__init__\.py)")


def search(q):
    out = subprocess.run(["gh", "api", "-X", "GET", "search/code", "-f", f"q={q}", "-f", "per_page=100"],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def main():
    rows, estimates = [], {}
    for line in (HERE / "search" / "queries.tsv").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, q = line.split("\t", 1)
        d = search(q)
        items = d["items"]
        own = {i["repository"]["full_name"] for i in items
               if not VENDORED.search(i["path"]) and "openai-python" not in i["repository"]["full_name"]}
        est = round(d["total_count"] * len(own) / len(items)) if items else 0
        estimates[key] = est
        rows.append(f"{key}\t{d['total_count']}\t{len(items)}\t{len(own)}\t{est}\t{q}")
        print(rows[-1])
        time.sleep(7)
    (HERE / "results" / "counts.tsv").write_text(
        "key\tfiles\tsample\town_repos_in_sample\test_repos\tquery\n" + "\n".join(rows) + "\n")
    (HERE / "results" / "estimates.json").write_text(json.dumps(estimates, indent=2) + "\n")


if __name__ == "__main__":
    main()
