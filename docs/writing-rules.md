# Writing a custom rule

The bundled rules cover what Airflow 3 changed for everyone. Your DAGs may also have their own patterns to find or change during the migration, such as:
- an internal helper that reads `context["execution_date"]`;
- a connection you renamed;
- a convention every DAG should follow.

A custom rule handles one of these. It's **one Python file, plus its test**.

## Where it goes

Put the rule file in `.airflow-verifier/rules/` in your repo, on your default branch. The action loads every `*.py` file there, except `test_*.py` and `_*.py`, and runs each one next to the bundled rules:

- **Verify mode** lists every site the rule finds, under *Airflow 2 code that breaks on Airflow 3*, with the rule's ID. Verify mode loads rules from the `before` tree, which is the PR's base branch. A pull request can't add a rule that runs inside the action; a rule takes effect once it's merged.
- **Migrate mode** lists the sites of a `FIX = "manual"` rule in its PR. It rewrites the sites of a `FIX = "deterministic"` rule. That rewrite then has to pass the same checks as the bundled rules: each DAG file is imported on Airflow 3, and the render check compares every template field of the changed tasks with Airflow 2. A rewrite that changes what a task renders is reverted and listed instead.

A rule file that fails to load doesn't stop the run. The report's *Other notes* says which file failed and why. To use another folder, set the `custom-rules` input. To turn custom rules off, set `custom-rules: none`.

## The file

```python
import ast
from pathlib import Path

from api_change_agent.rule_files import Site

ID = "require-dag-owner"            # lowercase letters, digits and dashes; shown on every finding
DESCRIPTION = "Every DAG sets `owner` in its default_args to a team, not Airflow's default `airflow`."
DOCS = "https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html#default-arguments"
FIX = "manual"                      # "manual": report each site. "deterministic": also rewrite it, with apply()


def find(path: Path, text: str) -> list[Site]:
    """Every site in one file. `path` is relative to the repo; `text` is the file's contents."""
    ...
    return [Site(line, snippet, reason)]
```

| Name | Required | What it is |
|---|---|---|
| `ID` | yes | Unique across all rules, bundled ones included. |
| `DESCRIPTION` | yes | One or two sentences. Shown for a site that has no `reason` of its own. |
| `DOCS` | yes | An http(s) link: where a reader learns why. In migrate mode's PR, every change links to it. |
| `FIX` | yes | `"manual"` or `"deterministic"`. |
| `find(path, text)` | yes | Returns a list of `Site(line, snippet, reason="")`. `line` is 1-based. `snippet` is the matched code. `reason` is what to do about it, shown to the reader. |
| `apply(path, text)` | for `"deterministic"` | Returns the whole file, rewritten. It must change only what `find` reports, so that running `find` on the result returns nothing. |
| `NOTE` | no | One line shown on each change in migrate mode's PR, such as "same connection, new name". |
| `FILES` | no | Template or config files the rule should also read, by file name, e.g. `("*.sql", "*.sh")`. The rule always reads every Python file that imports `airflow`. |

**Rules of thumb:**
- **Recognise the pattern from the source alone.** Parse with `ast` (or `libcst` when you rewrite), and don't import or run the file. The rule runs in the action's own process, not in the sandboxed venv where your DAGs are imported.
- **When you can't tell, don't guess.** If a value is defined elsewhere (`default_args=DEFAULTS`), skip it, or report it with a `reason` that says it couldn't be checked. A rule that cries wolf gets turned off.
- **Rewrite only when the change is mechanical and equivalent.** Anything that needs judgment belongs in a `"manual"` rule, with a `reason` that says what to decide.

## The test

Next to the rule, add a `test_<rule>.py` that loads it with `load_rule_file`. The action skips `test_*.py`, so the test can live in the same folder. Run it with pytest, in your repo's CI or locally after `pip install "git+https://github.com/patrickballard/airflow-migration-verifier@v1"`.

```python
from pathlib import Path

from api_change_agent.rule_files import load_rule_file

rule = load_rule_file(Path(__file__).parent / "require_dag_owner.py")


def test_finds_a_dag_without_an_owner():
    text = "from airflow import DAG\na = DAG('a', default_args={'retries': 1})\n"
    assert [s.line for s in rule.find(Path("dags/a.py"), text)] == [2]


def test_leaves_a_dag_with_an_owner_alone():
    text = "from airflow import DAG\na = DAG('a', default_args={'owner': 'data-platform'})\n"
    assert rule.find(Path("dags/a.py"), text) == []
```

Test at least:
- a snippet that should match;
- one that looks similar but shouldn't;
- for a `"deterministic"` rule, that `find` returns nothing on `apply`'s output.

## Examples

[`docs/example-rules/`](example-rules/) has two complete rules and their tests, which this repo's CI runs:
- [`require_dag_owner.py`](example-rules/require_dag_owner.py) (`FIX = "manual"`): DAGs whose `default_args` don't name an owner.
- [`rename_conn_id.py`](example-rules/rename_conn_id.py) (`FIX = "deterministic"`): rewrites any `*conn_id=` argument that uses a renamed connection.
- [`test_example_rules.py`](example-rules/test_example_rules.py): tests for both.

Copy one into `.airflow-verifier/rules/`, rename it, and adapt it.

## Having one built

A custom rule written for your codebase, with its tests, is the $1,000 one-time sponsor tier. [SPONSORS.md](../SPONSORS.md#1000-one-time-one-custom-migration-rule) lists what's included and the complexity limits.
