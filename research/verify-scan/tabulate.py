"""Summarise verify-mode runs on public repos (scan.sh) into results.json and a markdown table.

    python tabulate.py > table.md     # reads ~/SDK/real-world-airflow/_verify/<owner__name>/{result.json,commit}
"""
import collections
import json
import re
from pathlib import Path

V = Path.home() / "SDK" / "real-world-airflow" / "_verify"
HERE = Path(__file__).parent
REPOS = [line.split()[0] for line in (HERE / "repos.txt").read_text().splitlines() if line and not line.startswith("#")]
PINS = {line.split()[0]: line.split(None, 1)[1].strip() for line in (HERE / "repos.txt").read_text().splitlines()
        if line and not line.startswith("#")}

BREAK = {"ruff AIR301": "removed API", "ruff AIR302": "moved to a provider",
         "airflow3-context-keys": "removed context key", "airflow3-templates": "removed template variable",
         "airflow3-removed-dag-methods": "removed DAG method", "kwarg-check": "rejected argument"}


def one(slug: str) -> dict:
    d = V / slug.replace("/", "__")
    r = json.loads((d / "result.json").read_text())
    dags = r["dags"]
    files = {k.split("::")[0] for k in dags}
    notes = r.get("other_notes", [])
    by_kind = collections.Counter(BREAK.get(m["rule_id"], m["rule_id"]) for m in r["manual"])
    return {
        "repo": slug, "commit": (d / "commit").read_text().strip(), "airflow_2": PINS[slug],
        "dag_files": len(files),
        "import_on_airflow_3": len({k.split("::")[0] for k, v in dags.items() if v["imports"]}),
        "fail_on_airflow_3": len(r["unverified"]),
        "fail_on_both": sum(1 for n in notes if "doesn't import on Airflow 2 either" in n),
        "deprecated_not_counted": next((int(m.group(1)) for n in notes if (m := re.search(r"flags (\d+) import path", n))), 0),
        "breaks": sum(by_kind.values()), "by_kind": dict(by_kind.most_common()),
        "date_shifts": len(r["unpinned"]),
        "render_mismatch_dags": sum(1 for v in dags.values() if v["render"] == "mismatch"),
    }


if __name__ == "__main__":
    rows = [one(s) for s in REPOS]
    (HERE / "results.json").write_text(json.dumps(rows, indent=1) + "\n")
    print("| Repo | Airflow 2 | DAG files | Fail on Airflow 3 | Removed APIs in use | Run dates shift | Not counted (fail on Airflow 2 too) |")
    print("|---|---|---|---|---|---|---|")
    for x in rows:
        url = f"https://github.com/{x['repo']}/tree/{x['commit'][:7]}"
        print(f"| [{x['repo']}]({url}) | {x['airflow_2']} | {x['dag_files']} | {x['fail_on_airflow_3']} | {x['breaks']} | "
              f"{x['date_shifts']} | {x['fail_on_both']} |")
    tot = {k: sum(x[k] for x in rows) for k in ("dag_files", "fail_on_airflow_3", "breaks", "date_shifts", "fail_on_both",
                                               "deprecated_not_counted")}
    print(f"| **{len(rows)} repos** | | **{tot['dag_files']}** | **{tot['fail_on_airflow_3']}** | **{tot['breaks']}** | "
          f"**{tot['date_shifts']}** | {tot['fail_on_both']} |")
    kinds = collections.Counter()
    for x in rows:
        kinds.update(x["by_kind"])
    print("\nBy kind:", dict(kinds.most_common()), "| deprecated paths not counted:", tot["deprecated_not_counted"])
