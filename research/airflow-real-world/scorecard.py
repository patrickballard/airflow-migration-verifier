"""Build SCORECARD.md and scorecard_data.json from ~/SDK/real-world-airflow/_results.

    python scorecard.py

Safe to run at any point: repos without results are listed as not run. Hand-written parts
come from verdicts.json ({repo: {"verdict": ..., "why": ...}}) and findings.md.

Per DAG (a DAG id that loads on Airflow 2.11.2 with the original code):
  before  = it imports on Airflow 3.3.2 with the original code (the agent's baseline check)
  after   = it imports on Airflow 3.3.2 with the migrated code (the agent's final check)
  render  = every template field of it renders identically on both versions, runs paired by
            fire time (full_render.json); fields that only resolve inside a running task
            (XCom) or error on both versions aren't compared
Headline = DAGs with not before, after and render  /  DAGs with not before.
"""
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path.home() / "SDK" / "real-world-airflow"
RESULTS, SCAN = BASE / "_results", BASE / "_scan"
PIN = "airflow3-cron-timetable-pin"


def load(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def dags_in(outcome: dict | None) -> dict[str, str]:
    """dag id -> its file, for DAGs whose file imported."""
    out = {}
    for i in (outcome or {}).get("ids", []):
        if "::dag:" in i:
            f, d = i.split("::dag:", 1)
            if f"{f}::import" not in (outcome.get("failures") or []):
                out[f"{f}::{d}"] = f
    return out


def imports(outcome, key) -> bool:
    f = key.split("::")[0]
    return (outcome is not None and key.replace("::", "::dag:", 1) in outcome.get("ids", [])
            and f"{f}::import" not in outcome.get("failures", []))


def reason_kind(m: dict) -> str:
    r = m["reason"]
    if m["rule_id"].startswith("ruff "):
        return f"{m['rule_id']}: no automatic fix"
    if "render mismatch" in r:
        return "reverted: render mismatch"
    if "not attempted" in r or "no key" in r or "budget" in r.lower():
        return "needs Claude (" + ("budget" if "budget" in r.lower() else "not attempted") + ")"
    if m["rule_id"].endswith("judgment"):
        return f"{m['rule_id']}: Claude's patch not accepted" if "Claude" in r or "attempt" in r else m["rule_id"]
    return m["rule_id"]


def repo_record(repo: str) -> dict:
    name = repo.replace("/", "__")
    out = RESULTS / name
    rec = {"repo": repo, "ran": (out / "agent" / "result.json").exists()}
    sha = subprocess.run(["git", "-C", str(SCAN / name), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    rec["commit"] = sha.stdout.strip()
    timing = load(out / "timing.json") or {}
    rec["wall_s"] = timing.get("total_s") or sum(v for k, v in timing.items() if k.endswith("_s") and v)
    rec["agent_s"] = timing.get("agent_s")
    if not rec["ran"]:
        rec["error"] = timing.get("harness_error", "")[-500:]
        return rec
    base2 = load(out / "af2_baseline.json") or {}
    res = load(out / "agent" / "result.json")
    full = load(out / "full_render.json") or {}
    before, after = res.get("tests_before"), res.get("tests_after") or res.get("tests_before")
    rec["usd"] = (res.get("llm_usage") or {}).get("usd", 0)
    rec["llm_calls"] = (res.get("llm_usage") or {}).get("calls", 0)
    af2_files = sorted({i.split("::")[0] for i in base2.get("ids", []) if i.endswith("::import")})
    rec["af2_failed_files"] = {i.split("::")[0]: base2["messages"].get(i, "")[:160]
                               for i in base2.get("failures", []) if i.endswith("::import")}
    dags = dags_in(base2)
    fields: dict[str, list] = {}
    for f in full.get("fields", []):
        fields.setdefault(f"{f['file']}::{f['dag_id']}", []).append(f)
    per_dag = {}
    for key in sorted(dags):
        fs = fields.get(key, [])
        rendered = key in full.get("new_dags", []) and key in full.get("old_dags", [])
        per_dag[key] = {
            "before": imports(before, key), "after": imports(after, key),
            "fields": len(fs), "mismatched_fields": sum(1 for f in fs if f["n_mismatches"]),
            "render_ok": rendered and not any(f["n_mismatches"] for f in fs),
            "dag_test_af2": key.replace("::", "::dag:", 1) not in base2.get("failures", []),
            "dag_test_after": after is not None and key.replace("::", "::dag:", 1) not in after.get("failures", []),
        }
    rec["dag_files"] = len(af2_files)
    rec["dags"] = per_dag
    rec["pins"] = sum(1 for r in res["rows"] if r["rule_id"] == PIN and "CronDataIntervalTimetable(" in r["after"])
    rec["claude_pins"] = sum(1 for r in res["rows"] if r["rule_id"] == "airflow3-timetable-judgment"
                             and "IntervalTimetable(" in r["after"] and "import" not in r["after"])
    rec["pin_reverted"] = sum(1 for m in res["manual"] if m["rule_id"] == PIN and "render mismatch" in m["reason"])
    rec["rows"] = len(res["rows"])
    rec["rules"] = Counter(r["rule_id"] for r in res["rows"])
    rec["manual"] = Counter(reason_kind(m) for m in res["manual"])
    rec["manual_n"] = len(res["manual"])
    rec["mismatches"] = [f"{f['dag_id']}.{f['task_id']}.{f['field']}: {f['summary']}"
                         for f in full.get("fields", []) if f["n_mismatches"]][:8]
    rec["timezone"] = full.get("timezone")
    rec["new_failures"] = res.get("new_failures", [])
    rec["render_error"] = full.get("error")
    return rec


def headline(recs):
    tot = fixed = already = still_ok = 0
    for r in recs:
        for d in (r.get("dags") or {}).values():
            if d["before"]:
                already += 1
                still_ok += d["after"] and d["render_ok"]
                continue
            tot += 1
            fixed += d["after"] and d["render_ok"]
    return tot, fixed, already, still_ok


def fmt_s(s):
    return f"{s // 60}m{s % 60:02d}s" if s else "—"


def main():
    repos = [ln.split()[0] for ln in (HERE / "repos.txt").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    recs = [repo_record(r) for r in repos]
    verdicts = load(HERE / "verdicts.json") or {}
    (HERE / "scorecard_data.json").write_text(json.dumps(recs, indent=1, default=dict))
    tot, fixed, already, still_ok = headline(recs)
    done = sum(r["ran"] for r in recs)
    spend = sum(r.get("usd") or 0 for r in recs)
    # every run that ever spent, including passes whose results were replaced (run*.log lines: "spent=$x")
    all_spend = sum(float(m.group(1)) for log in HERE.glob("run*.log")
                    for m in re.finditer(r"spent=\$([\d.]+)", log.read_text()))
    lines = ["# api-change-agent: Airflow 2 → 3, real-world dry run", ""]
    if done < len(recs):
        lines += [f"**Partial: {done} of {len(recs)} repos have results.**", ""]
    lines += [
        f"{len(recs)} public repos with Airflow 2 DAGs, cloned locally (`~/SDK/real-world-airflow`, not committed). "
        "Each ran `--dry-run` with the render check on: Airflow 2.11.2 vs 3.3.2, Python 3.12, official constraints "
        "plus each repo's declared and imported providers. Nothing was pushed and no PRs were opened. "
        "Scripts: `find_candidates.py`, `run_repo.py`, `run_all.py`, `scorecard.py`.", "",
        f"**Headline: {fixed} of {tot} DAGs ({100 * fixed / tot:.0f}%) went from failing on Airflow 3 to importing on "
        f"Airflow 3 with templates rendering identically to Airflow 2.**" if tot else "**Headline: no DAGs scored yet.**",
        "",
        f"Counted over DAGs that load on Airflow 2.11.2. {already} more already imported on Airflow 3 before the "
        f"migration; {still_ok} of them still import and render identically after it. "
        f"Claude spend: ${spend:.2f} for the runs in this table; ${all_spend:.2f} in all, counting the runs they "
        "replaced (see Runs below), within the $5.00 limit.", "",
        "| Repo | DAGs | AF3 import before → after | Pins | Render check | Manual items | Time | Claude | Verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in recs:
        v = verdicts.get(r["repo"], {})
        if not r["ran"]:
            lines.append(f"| {r['repo']} | — | not run | | | | {fmt_s(r.get('wall_s'))} | | {v.get('verdict', '—')} |")
            continue
        d = r["dags"].values()
        n = len(r["dags"])
        b, a = sum(x["before"] for x in d), sum(x["after"] for x in d)
        cmp_ = [x for x in d if x["after"]]
        ok = sum(x["render_ok"] for x in cmp_)
        nf = sum(x["fields"] for x in cmp_)
        bad = sum(x["mismatched_fields"] for x in cmp_)
        render = f"{ok}/{len(cmp_)} DAGs identical ({nf} fields" + (f", {bad} mismatched" if bad else "") + ")"
        man = f"{r['manual_n']}: " + "; ".join(f"{k} ×{c}" for k, c in r["manual"].most_common(4)) if r["manual_n"] else "0"
        excl = f" (+{len(r['af2_failed_files'])} files excluded)" if r["af2_failed_files"] else ""
        pins = (f"{r['pins']}" + (f" + {r['claude_pins']} by Claude" if r.get("claude_pins") else "")
                + (f" ({r['pin_reverted']} reverted)" if r["pin_reverted"] else ""))
        lines.append(f"| [{r['repo']}](https://github.com/{r['repo']}/tree/{r['commit']}) | {n}{excl} | {b}/{n} → {a}/{n} "
                     f"| {pins} | {render} | {man} | {fmt_s(r['wall_s'])} | ${r['usd']:.2f} | "
                     f"**{v.get('verdict', 'not reviewed')}** {v.get('why', '')} |")
    lines += ["", "Pins: cron/delta schedules pinned to Airflow 2's timetable, deterministic + by Claude. "
              "Render check: DAGs that import after the migration, every template field compared. "
              "Time is wall-clock for the whole repo (venvs, Airflow 2 baseline, agent, full render); "
              "Claude is that repo's spend.", ""]
    findings = HERE / "findings.md"
    if findings.exists():
        lines += [findings.read_text().strip(), ""]
    lines += ["## Excluded: DAG files that don't load on Airflow 2.11.2", ""]
    for r in recs:
        for f, why in (r.get("af2_failed_files") or {}).items():
            lines.append(f"- `{r['repo']}` `{f}`: {why}")
    lines += ["", "## Render mismatches (full comparison)", ""]
    for r in recs:
        for m in r.get("mismatches") or []:
            lines.append(f"- `{r['repo']}` {m[:300]}")
    (HERE / "SCORECARD.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:12]))


if __name__ == "__main__":
    main()
