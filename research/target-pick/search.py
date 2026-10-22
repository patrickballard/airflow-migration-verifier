"""Estimate public repos still using each library's OLD-major patterns (needs `gh`).

    python search.py   # ~2-3 min: code search allows ~10 requests/min
Counts are matching files on default branches (GitHub's estimate). Code search
is case-insensitive and splits on punctuation, so each hit's matched fragments
are checked for the exact, case-sensitive pattern; "precision" is the share that
really contain it. Repos ~= total * precision * distinct-repos / sample, after
dropping hits inside copies of the library itself (forks, vendored, site-packages).
"""
import json, pathlib, re, subprocess, time

HERE = pathlib.Path(__file__).parent
PKG_DIR = {"pydantic": "pydantic", "sqlalchemy": "sqlalchemy", "numpy": "numpy", "pandas": "pandas",
           "langchain": "langchain", "llamaindex": "llama_index", "airflow": "airflow", "marshmallow": "marshmallow"}
INTERNAL = r"(core|_[a-z_]+|internal|v1|types|engine|orm|sql|operators|models|providers|utils|chains|agents|fields|schema)"


def vendored(lib, item):
    path, repo = item["path"], item["repository"]["full_name"].lower()
    if re.search(r"(site-packages|dist-packages|(^|/)\.?venv/|(^|/)env/|lib/python[\d.]+/)", path):
        return True
    if repo.split("/")[1] in (lib, PKG_DIR[lib], PKG_DIR[lib].replace("_", "-"), f"{lib}-python", f"apache-{lib}"):
        return True  # forks of the library itself
    return bool(re.search(rf"(^|/){PKG_DIR[lib]}/{INTERNAL}(/|\.py)", path))


rows, out = [], {}
for line in (HERE / "search" / "queries.tsv").read_text().splitlines():
    if not line.strip() or line.startswith("#"):
        continue
    lib, key, q, exact = line.split("\t")
    r = subprocess.run(["gh", "api", "-X", "GET", "search/code", "-H", "Accept: application/vnd.github.text-match+json",
                        "-f", f"q={q}", "-f", "per_page=100"],
                       capture_output=True, text=True)
    if r.returncode:
        print(lib, key, "ERROR", r.stderr.strip()[:120]); time.sleep(10); continue
    d = json.loads(r.stdout)
    items = d.get("items", [])
    real = [i for i in items if any(exact in m.get("fragment", "") for m in i.get("text_matches", []))]
    precision = len(real) / len(items) if items else 0
    own = {i["repository"]["full_name"] for i in real if not vendored(lib, i)}
    est = round(d["total_count"] * len(own) / len(items)) if items else 0
    out.setdefault(lib, {})[key] = {"files": d["total_count"], "sample": len(items), "precision": round(precision, 2),
                                    "own_repos": len(own), "est_repos": est, "query": q, "exact": exact}
    print(f"{lib:12} {key:20} files={d['total_count']:>7} precision={precision:4.0%} own_repos={len(own):>3}/{len(items)} est_repos~{est}")
    time.sleep(7)
(HERE / "results" / "search.json").write_text(json.dumps(out, indent=1) + "\n")
