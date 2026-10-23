"""Print one repo's results: python show.py owner__name"""
import json
import sys
from pathlib import Path

B = Path.home() / "SDK" / "real-world-airflow" / "_results" / sys.argv[1]
r = json.loads((B / "agent" / "result.json").read_text())
print("usage", r["llm_usage"])
for k in ("tests_before", "tests_after"):
    t = r[k]
    print(k, t and t["summary"])
    for i in (t or {}).get("ids", []):
        if i in t["failures"]:
            print("   FAIL", i, t["messages"].get(i, "")[:140])
for m in r["manual"]:
    print("MANUAL", m["path"], m["line"], m["rule_id"], m["reason"][:220])
for x in r["rows"]:
    print("ROW", x["path"], x["line"], x["rule_id"], x["note"][-110:])
print("notes", r["other_notes"], r["skipped_rules"])
f = json.loads((B / "full_render.json").read_text())
print({k: v for k, v in f.items() if k not in ("fields", "old_dags", "new_dags")}, "old", len(f.get("old_dags", [])),
      "new", len(f.get("new_dags", [])), "fields", len(f.get("fields", [])))
for x in f.get("fields", []):
    if x["n_mismatches"]:
        print("MISMATCH", x["dag_id"], x["task_id"], x["field"], x["summary"][:250])
