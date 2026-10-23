## This round (2026-10-23): the top gaps fixed

Five things changed since the previous scorecard:

1. **`days_ago(N)`** now becomes `pendulum.today("UTC").add(days=-N)`. That is the same moment (UTC midnight today minus N days), still computed at parse time; a fixed date would change scheduling and catchup. It was checked against Airflow 2's own `days_ago` on its own version, and DAGs that use it are render-checked as a whole.
2. **The cron pin reads `start_date` the way Airflow 2's `DAG.__init__` does:** from the DAG, else from `default_args`, else from module-level values. It also understands `days_ago` and `pendulum.today("Z")`.
3. **Cron and timedelta DAGs left unpinned** are render-checked as a whole and listed under "⚠️ Run dates will shift".
4. **Files that fail on Airflow 3 before and after** are rebuilt without ruff's unsafe fixes or Claude's patches. Their remaining changes are marked unverified, and the file is listed with its error.
5. **Three more bugs the rerun found, fixed in separate commits:**
   - a Python set printed in a per-process order;
   - an unset trigger date: `None` on Airflow 2 versus `NOTSET` on Airflow 3, which mean the same thing;
   - the unverified rebuild re-applying changes the render check had reverted (`624c918`).

**Only 55 of the 146 DAGs** had at least one template field to compare. The other 91 have no templated fields, so "renders identically" is vacuous for them. For those, the evidence is the DagBag import plus the pin and `days_ago` proofs.

**dag.test():** 66 DAGs both run locally on Airflow 2 and import on Airflow 3 after the migration. 58 of them also pass `dag.test()` on Airflow 3. Of the other 8:
- 4 are BasPH's rocket-launch DAGs, which call a live external API (`KeyError: 'results'`). Not ours.
- 2 read a removed context key the agent listed as manual: `next_execution_date`, and a callable parameter named `execution_date`. Claude's budget ran out before them.
- 2 call `DAG.following_schedule`, which Airflow 3 removed. **Neither ruff nor our rules flag it.** Those DAGs import and render identically, so they count in the headline, but they would fail at run time.

`dag.test()` failing on connections or credentials on both versions isn't counted against us.

## What's left (ranked by DAGs)

| # | Gap | DAGs | Note |
|---|---|---|---|
| 1 | Removed provider classes (`PostgresOperator`, `SnowflakeOperator`, `SubDagOperator`, ...) | ~7 scored (many more among excluded files) | Manual by nature; these files now show up as unverified. |
| 2 | ruff marks `MySqlOperator` → `SQLExecuteQueryOperator` **safe**, but it keeps `mysql_conn_id=` | AMPATH | The unverified label keeps it from being presented as done. A kwargs check on class swaps would catch it outright. |
| 3 | `prev_ds` / `next_ds` with no exact equivalent | BasPH 2 fields | Claude's patch was rejected; stays manual. |
| 4 | `days_ago(n, h)` with a positional `hour` | made-ml 1 | Goes to judgment; Claude got it right. A small rule extension. |
| 5 | Computed schedules (`schedule=export_schedule_interval`) | blockchain-etl 3 | Listed under "Run dates will shift". |
| 6 | `DAG.following_schedule()` (removed in Airflow 3) isn't detected | BasPH 2 | **Unflagged**: the DAGs import and render, but fail at run time. Needs a rule (judgment: `dag.timetable.next_dagrun_info` or `data_interval_end`). |

## Runs and spend

- **Earlier scorecard:** $4.10 (passes 1–3, see git history).
- **This round, deterministic only ($0):**
  - pass 4: superseded by the fixes it found;
  - pass 5: all 10 repos.
- **This round, with Claude ($0.60 of the $0.90 cap):** pass 6 on the six repos with judgment sites in verified files. That's 15 calls at about $0.04 each.
- **Total:** $4.70 of the $5.00 limit.
- **Time:** BasPH's Claude pass took 97 minutes, because every patch re-ran the full 87-DAG check. The next commit makes that check re-test only the patched file.

## Harness limits

- Provider versions come from the repo's era (its Airflow pin, else its image tag, else its last-commit date).
- 60 DAG files that don't load on Airflow 2.11.2 are excluded from the score and listed below.
- Repo code ran locally with a throwaway HOME and no credentials in its environment.
