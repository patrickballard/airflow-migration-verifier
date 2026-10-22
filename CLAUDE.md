# api-change-agent — standing rules
- Never push to or open PRs on repos not owned by patrickballard. Real-world tests stay local.
- Never print, log, or commit secrets. Tests must never reach the real key file.
- Claude API spend: default cap $1 per task unless told otherwise. Always report spend.
- Deterministic rewrites only when proven value-preserving; otherwise manual with a reason.
- Verify every fix: tests before/after; Airflow also DagBag + dag.test() + render check.
- Order: plan → tests → code. Commit bug fixes separately from features.
- Report: what changed, what's manual and why, verification results, spend, risks. Short.
