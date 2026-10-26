# Security

## Reporting a vulnerability

Please report it privately, not in a public issue: use [**Report a vulnerability**](https://github.com/patrickballard/airflow-migration-verifier/security/advisories/new) (GitHub's private vulnerability reporting, under the repo's *Security* tab). Only the maintainer sees the report.

Include what you found, how to reproduce it, and which version (`@v1`, or a `v1.x.y` tag) you used. You'll get a reply within 7 days. Fixes go into the latest `v1.x.y` release, and `@v1` moves to it.

## What the action accesses

- **Your repo's files.** It reads the checked-out workspace (in verify mode, the `before` and `after` trees).
  - It imports your DAG files and renders their templates on Airflow 2 and 3, so your DAG code runs in the job, as it would in your own CI.
  - Migrate mode also runs `dag.test()` on each DAG; `test-command: none` turns that off.
- **The job's token** (`github-token`, default `${{ github.token }}`). Only migrate mode uses it, to push its own branch (`api-change-agent/<package>`; it refuses the default and base branches) and to open or update its PR through the GitHub API. Verify mode and `dry-run: "true"` don't use it.
- **`ANTHROPIC_API_KEY`**, optional and migrate mode only. Without it, Claude is never called. With it, each file that has a judgment site is sent to Anthropic's API under your key, within `max-llm-usd`. The key is never printed, and it's removed from the environment your repo's code runs in.

**Nothing is sent anywhere else.** There's no telemetry. The job downloads the action's base image (`python:3.12-slim`), Airflow and your requirements from PyPI, and Airflow's constraints files from `raw.githubusercontent.com/apache/airflow`. None of those requests carry your code.

**Pull requests from forks:** for `pull_request` runs from a fork, GitHub gives the job a read-only token and no secrets. The verify workflow in the README needs nothing more.
