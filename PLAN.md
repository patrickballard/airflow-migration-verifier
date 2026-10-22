# api-change-agent — plan

A Docker GitHub Action that finds outdated `openai` Python SDK usage in a repo,
fixes it (deterministically, or with Claude), checks the result against the
repo's own tests, and opens a PR. It never pushes to the default branch.

## 0. Probe result (openai 3.36.1, 2026-10-22)

`scripts/probe_beta_path.py` calls `parse`/`stream` against a local mock server:

| Call | Result |
|---|---|
| `client.beta.chat.completions.parse` (sync + async) | works, **no warning** |
| `client.beta.chat.completions.stream` | works, **no warning** |
| `functions=` / `function_call=` on `chat.completions.create` | accepted; deprecated in docstrings only, no runtime warning |

`Beta.chat` is a `cached_property` that returns the regular `Chat` resource, so
the two paths are the same object type. So Rule 1 is forward-compat cleanup
(nothing breaks today) and the PR text says so; it does not claim a fix for a
breakage.

## 1. Rules file (`rules/openai.yaml`)

```yaml
package: openai
rules:
  - id: beta-chat-completions-to-ga
    match:   { attribute: "*.beta.chat.completions.{parse,stream}" }
    fix: deterministic
    transform: { drop_attribute: beta }
    min_sdk_version: "1.92.0"
    changelog: https://github.com/openai/openai-python/releases/tag/v1.92.0
  - id: functions-to-tools
    match:   { call: "*.chat.completions.{create,parse,stream}", kwargs: [functions, function_call] }
    fix: llm
    guidance: |  # migration notes passed to Claude
    min_sdk_version: "1.1.0"
    changelog: https://github.com/openai/openai-python/releases/tag/v1.1.0
```

Pattern language for `attribute` / `call`: a dotted chain where a leading `*`
matches any receiver expression (`client`, `self.client`, `get_client()`), a
middle `*` matches one name, and `{a,b}` is alternation. `match.attribute` hits
any attribute chain (called or not); `match.call` hits only calls, and
`kwargs` narrows that to calls that pass at least one of the listed keywords.

## 2. Pipeline

1. **Scan.** Walk `*.py`, skipping `.git`, venvs, `node_modules`, `build`,
   `dist` and user `exclude` globs. A cheap substring check comes first, then a
   libcst import check: keep the file only if it has `import openai[.x]` or
   `from openai[.x] import …` anywhere (including inside functions and
   `try`). Files that don't parse are skipped and listed.
2. **Baseline tests.** If any rule matched and tests are enabled, run the
   test command once *before* changing anything. Pytest runs write junit XML,
   so the agent compares individual test IDs: only *new* failures count.
3. **Deterministic rules** (libcst `CSTTransformer` + `PositionProvider`).
   `drop_attribute: beta` removes exactly the `.beta` node at the pattern's
   position. Comments, whitespace and parenthesized multi-line chains stay
   untouched, and string or docstring mentions are never touched (they aren't
   `Attribute` nodes).
4. **Pins.** The required version is the highest `min_sdk_version` among
   rules that produced changes. The agent edits text in place and keeps
   formatting in:
   - `requirements*.txt` / `*.in` / `constraints*.txt` / `requirements/*.txt`
   - `pyproject.toml` (PEP 621 lists and Poetry `openai = "^x"` / `{version = …}`)
   - `setup.cfg`, and string literals in `setup.py`

   How each specifier changes:

   | Specifier | Result |
   |---|---|
   | `==1.50.0` | `==1.92.0` |
   | `>=1.40` | `>=1.92.0` |
   | `~=1.40` | `~=1.92` (same precision) |
   | Poetry `^1.40` | `^1.92.0` |
   | `<2` with no lower bound | appends `>=1.92.0` |
   | bare `openai` | left alone (not pinned) |
   | `<1.80`, `<=1.91`, `===1.50` | **conflict**: the rule that needs it is skipped and the PR says why |

   Lock files (`poetry.lock`, `uv.lock`, `Pipfile.lock`, `pdm.lock`) are
   never rewritten. If one locks a version that's too old, the PR body says to
   regenerate it. `openai-agents`, `langchain-openai` and similar packages
   never match.
5. **Install.** Tests run in a separate venv (`/tmp/aca-venv`), so the repo's
   dependencies can't break the action's own. The install command defaults
   to `pip install -r requirements.txt` and/or `pip install -e .` plus
   `pytest`, and runs again after a pin bump.
6. **LLM rules**, one file at a time:
   1. Send Claude the whole file, the flagged call sites (line + snippet) and
      the rule's `guidance`.
   2. Claude returns the full file through a forced `submit_patch` tool call
      (streamed, so large files don't hit the non-streaming limit).
   3. Validate the patch: it must parse with libcst, must change something,
      and the rule's detector must find no remaining sites.
   4. Write the file and run the tests. A new failure feeds the failing test
      IDs and output tail back to Claude for a retry (`max-llm-attempts`,
      default 2).
   5. If it still fails, revert the file and list it under "Needs manual
      migration".

   With no `ANTHROPIC_API_KEY`, LLM sites go straight into that list.
7. **Report.** The table has one row per change: `file:line` (linked to the PR
   branch), `before → after`, rule, and changelog link. Pin edits get rows
   too. Then come sections for test results (baseline vs after), skipped
   rules with reasons, manual items, and lockfile notes. Line numbers from the
   deterministic pass are mapped through later LLM edits with `difflib`.
   The same markdown goes to `$GITHUB_STEP_SUMMARY`.
8. **Git / PR.**
   - Branch `api-change-agent/openai` is cut from the base branch.
   - Commits are made as `github-actions[bot]`, and pushes use the explicit
     refspec `HEAD:refs/heads/<branch>`.
   - `ensure_safe_branch()` runs right before every push. It raises if the
     target is `main`, `master`, the repo's default branch, or the base
     branch, so a misconfigured input can't push to main.
   - If an open PR from that branch already exists, it is updated:
     force-with-lease against the fetched sha, then the body is PATCHed.
     Nothing is pushed if the tree is unchanged.
   - `dry-run: true` stops before any git write and only prints the report.

## 3. Layout

```
action.yml  Dockerfile  pyproject.toml  rules/openai.yaml
src/api_change_agent/
  rules.py      load + validate YAML
  patterns.py   chain pattern parse/match
  scanner.py    file walk + "imports openai" check
  codemod.py    deterministic transforms, site finders
  pins.py       specifier bumping + dependency-file edits
  llm.py        PatchModel protocol + ClaudePatchModel
  testrun.py    venv, install, test run, junit diff
  report.py     PR body
  gitops.py     branch/commit/push with main guard
  github_api.py minimal REST client (find/create/update PR)
  agent.py      orchestration
  cli.py / __main__.py
tests/
  fixtures/sample_repo/   app importing openai (rule 1 + rule 2), a
                          non-openai file, requirements.txt pinning 1.50.0,
                          and its own pytest suite with a fake client
  fixtures/expected/      golden output for the deterministic rewrite
  fixtures/llm/           canned "good" and "bad" Claude patches
  test_*.py
```

## 4. Tests (written before the code)

- **Patterns:** wildcard receivers, alternation, no match on `create` or
  partial chains.
- **Scanner:** picks up `import openai`, `from openai.types import …` and
  imports inside functions; ignores `openai_agents` and files that don't
  import openai.
- **Deterministic:** golden-file equality, comments preserved, docstring
  untouched, multi-line chain, async, and the line numbers reported.
- **Pins:** a table of specifier cases; files edited in place; lookalike
  packages untouched; conflicts reported; lock notes.
- **LLM flow** (fake model, real fixture tests in a subprocess):
  - a good patch is kept and tests pass;
  - a patch that breaks tests is retried with feedback and then reverted;
  - a patch that doesn't parse is rejected;
  - no key means the sites are listed as manual.
- **Report:** escaping `|` and `<`, links, multi-line cells.
- **Git:** `ensure_safe_branch` refuses main, master, the default branch and
  the base branch. End-to-end: a fixture copy with a local bare remote and a
  fake GitHub client ends with the remote `main` sha unchanged, the bot
  branch pushed, and a PR body with the table.

## 5. Known limits (stated in README)

- Aliased chains aren't followed (`c = client.beta.chat.completions;
  c.parse(...)`), and neither are files that use a client without importing
  openai. This follows the "only files that import openai" rule.
- Only the repo's own test suite guards LLM patches. If there are no tests,
  the PR says the changes are untested.
