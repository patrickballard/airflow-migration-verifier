# Contributing

Bug reports are the most useful contribution, especially [**It missed something**](https://github.com/patrickballard/airflow-migration-verifier/issues/new?template=missed_break.yml): a break on Airflow 3 that the verifier didn't flag. For questions and ideas, use [Discussions](https://github.com/patrickballard/airflow-migration-verifier/discussions).

## Running the tests

```bash
pip install -e '.[test]' && pytest
```

The tests that need real Airflow skip themselves unless you point them at an Airflow 2 and an Airflow 3 venv, built with the official constraints, as CI does:

```bash
for v in 2.11.2 3.3.2; do
  python3.12 -m venv ~/.aca-venvs/airflow-${v%%.*}
  ~/.aca-venvs/airflow-${v%%.*}/bin/pip install "apache-airflow==$v" \
    -c "https://raw.githubusercontent.com/apache/airflow/constraints-$v/constraints-3.12.txt"
done
ACA_AIRFLOW2_VENV=~/.aca-venvs/airflow-2 ACA_AIRFLOW3_VENV=~/.aca-venvs/airflow-3 pytest
```

CI runs all of that, plus the mutation suite, [nightly in a separate repo](https://github.com/patrickballard/api-change-agent-test/actions/workflows/ci.yml): a Marketplace action's repo can't contain workflow files, so **don't add any under `.github/workflows/`**. The maintainer runs CI on your branch before merging.

## Rules must be proven value-preserving

A rule may rewrite code on its own only when the rewrite provably keeps the same values. Otherwise it lists the site for a human, with the reason.
- **Proof is the render check.** Every template field is rendered with the old code on Airflow 2 and the new code on Airflow 3, run by run, and must come out identical. A Python-side rewrite needs a documented equivalence (cite it in the rule's `changelog`), plus the DAG import and `dag.test()` on Airflow 3.
- **The render check itself is guarded by [the mutation suite](tests/test_render_mutations.py).** Each mutation is a wrong migration that must never read as "identical". If you change the render check, the suite must still catch every mutation. If you find a wrong migration it misses, add it as a mutation.
- **Every fix and every rule comes with tests**, written first: one that fails before the change and passes after it, and a real-Airflow test where the behaviour depends on Airflow.

## Commits

- **Tests first**, then code, in the same commit.
- **Bug fixes go in their own commits,** separate from features and from docs.
- **Message:** a short subject naming the area and what changed, such as `verify: ...`, `render check: ...` or `README: ...`. Then a body saying what was wrong and why the change fixes it.
- **Never commit secrets.** Tests must never reach a real API key: the test runner strips anything named like a credential from the environment that repo code sees.
