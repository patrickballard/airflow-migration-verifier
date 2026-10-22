# api-change-agent: real-world dry run (2026-10-22)

10 public repos, cloned locally. On a local branch `aca-sim/openai-3.36.1`,
each one's openai pin was raised to 3.36.1, the simulated upgrade.
Poetry `^3.36.1` resolved to 3.37.0. The agent then ran
`--dry-run --output`. Nothing was pushed and no PRs were opened.

`ANTHROPIC_API_KEY` was not set, so only the deterministic rules ran.
Claude spend: $0.

Per repo: `_results/<repo>/changes.diff`, `pr_body.md`, `result.json`, `log.txt`.

| Repo | Rules fired | Tests before → after | Verdict |
|---|---|---|---|
| vale95ntino/cot2tot | beta → GA (1) | 37 passed → 37 passed | **clean** |
| grasp-technologies/grasp-agents | beta → GA (4: parse + stream, src and legacy) | 2846 passed / 22 failing / 21 coll. → identical | **clean** (failures: Gemini/Anthropic/Vertex extras not installed) |
| MalarzDawid/KFT | beta → GA (1) | 23 passed / 2 failing / 1 coll. → identical | **clean** (failures: pygame headless) |
| CharlesQ9/HistAgent | beta → GA (3, incl. module-level `openai.beta…`) | no test suite (only vendored browser_use tests) | **clean**, untested; module-level path verified |
| DavidTokar12/SkyAgent | declare-httpx (Poetry, + `poetry.lock` note) | 16 passed / 1 failing / 4 coll. → identical | **clean**, low impact: `anthropic` also installs httpx |
| TioeAre/BayesRAG | none (httpx already declared) | no test suite (eval scripts only) | **nothing to do**, correct |
| thichcode/mythuky | none (httpx already declared) | 4 passed / 2 coll. | **nothing to do**, correct |
| Packstack-Tech/packstack-api | functions-to-tools flagged (1), no key | 120 passed / 11 coll. (need Postgres) | **not attempted**: needs Claude |
| robocorp/llmstatemachine | functions-to-tools flagged (2), no key | 0 passed / 11 failing (examples) | **not attempted**: needs Claude |
| KTS-o7/Compliance-agent | none | — | **miss**: beta call in `evaluator.py`, which uses an injected client and doesn't import openai |

## Hit rate

- **Precision: 5/5 diffs clean.** None needed a human tweak and none were
  wrong. No test changed result in any of the four repos with runnable
  tests.
- **Deterministic recall: 6/7 repos.** 5 fixed, 2 correctly left alone,
  1 miss (KTS-o7). By site: 9/10 beta call sites; 1/1 missing httpx
  declaration.
- **Overall: 7/10 repos handled correctly.** Of the other 3, 2 were LLM-only
  and need an API key; 1 was a miss.

## What the agent didn't cover (upgrade blockers)

- **Frozen lock files.** openai 3.x needs `anyio>=4.10`, `jiter>=0.16`,
  `typing-extensions>=4.14` and a newer `idna`. KFT's and grasp-agents'
  fully pinned `requirements.txt` couldn't resolve until regenerated (done
  by hand in the simulation).
- **Wheels on new Pythons.** `psycopg2-binary==2.9.9` has no 3.13 wheel
  (Packstack); it ran on 3.12 instead.
- **Tests that mock the old path.** KTS-o7's tests mock
  `client.beta.chat.completions.parse`. A rewrite there would make those
  mocks miss; the test gate should catch that.

## Agent fixes made during this run (all pushed)

- `7934268`:
  - `--output` writes the diff, PR body and summary;
  - credential-like env vars are stripped from the test environment;
  - `--venv-python` picks the test venv's interpreter.
- `cd08de3`:
  - auto-install covers dev/test dependency groups (SkyAgent needed
    vcrpy);
  - tests get a dummy `OPENAI_API_KEY`;
  - the empty log line is fixed.
- `9de26ed`: pytest gets `--continue-on-collection-errors`, and the summary
  counts the tests that ran. Before this, grasp-agents' gate ran **0**
  tests.

## Reproducing

From this folder (needs an authenticated `gh` and `uv`):

1. `python find_candidates.py > candidates.json`: search and filter; the
   2026-10-22 output is committed.
2. Clone the picked repos into `~/SDK/real-world/<owner>__<repo>`, then run
   `python simulate_upgrade.py <clone>`. That commits the openai bump on a
   local branch.
3. Where pip can't resolve a frozen lock file, run
   `python relax_openai_deps.py "<openai Requires-Dist;…>" <requirements files>`
   and commit just those files. For a uv-compiled lock, re-run
   `uv pip compile`.
4. `./run_one.sh <owner>__<repo>`. Set `VENV_PYTHON=$(uv python find 3.12)`
   for repos that need an older Python. This resets the clone, then runs the
   agent with `--dry-run --output ~/SDK/real-world/_results/<repo>`.
