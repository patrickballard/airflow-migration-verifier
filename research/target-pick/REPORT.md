# Which library should api-change-agent target next?

Research only, 2026-10-22. This uses the same evidence approach as
[`../openai-breaks`](../openai-breaks/):
- one venv per old and new major;
- proof snippets that run old code on both versions;
- the existing codemods run on old code;
- GitHub code search for code still on the old major.

## Recommendation: **Apache Airflow 2 → 3**

| Rank | Library (old → new) | Score /100 | One-line case |
|---|---|---|---|
| **1** | **Airflow 2.11 → 3.3** | **84** | Airflow 2 is already EOL, a well-funded vendor needs its customers off it, ruff only fixes the easy half, and DAGs are cheap to verify |
| 2 | LangChain 0.3 → 1.4 | 71 | No working tool (the official CLI is broken and has no 1.0 rules), a rich vendor, but many hobby repos and a `langchain-classic` escape hatch |
| 3 | pandas 2.3 → 3.1 | 70 | Biggest stuck base, zero tooling, the most *silent* breaks; but no vendor to pay and little test coverage (notebooks) |
| 4 | SQLAlchemy 1.4 → 2.1 | 63 | No codemod, a silent data-loss break (autocommit); no vendor, and 2.0 is 3 years old |
| 5 | Pydantic 1 → 2 | 61 | bump-pydantic does a lot but is stale (last release 2023-12) and makes mistakes; v2 is 3 years old |
| 6 | LlamaIndex 0.9 → 0.14 | 56 | Huge API churn, but a small stuck base and the official CLI is deprecated |
| 7 | NumPy 1 → 2 | 44 | Huge usage, but ruff `NPY201` already fixes most of what can be fixed |
| 8 | marshmallow 3 → 4 | 44 | Clean breaks, tiny stuck base, nobody to pay |

**Weights:**

| Factor | Weight | 5 means |
|---|---|---|
| Who pays | 25 | a funded vendor plus enterprise budgets |
| Tooling gap | 20 | nothing usable exists |
| Stuck users | 15 | largest estimated repo count |
| LLM share | 15 | ≥60% of breaks need context |
| Urgency | 15 | the old major is past EOL |
| Verifiability | 10 | a cheap, reliable test gate |

Each library scores 1–5 per factor, and the total is the weighted sum
scaled to 100 (the factor scores are in [Scoring detail](#scoring-detail)).
The weights are judgment calls, so the raw evidence follows and the table
can be re-weighted.

### Why Airflow first

1. **Hard deadline.** OSS Airflow 2 reached **EOL on 2026-04-22**. Astronomer
   supports Airflow 2 on Astro only **through April 2027**, and AWS MWAA
   already ended several 2.x versions on 2025-12-30. Nobody else in the
   table has a dated cliff.
2. **Someone loses money if customers don't upgrade.**
   - Astronomer raised a **$93M Series D** (May 2025) and reports **Astro ARR
     up 150% year on year, with 130% net revenue retention**. Customers stuck
     on 2.x are retention risk.
   - MWAA and Cloud Composer have the same problem.
   - The buyer is a data-platform team with hundreds of DAGs and a budget.
3. **The free tool stops exactly where our moat starts.** On our sample, ruff
   `AIR` rewrote the imports (`airflow.sdk`, `providers.standard`) and
   `schedule_interval` → `schedule`, and the DAG then **loads**. But it left
   `context["execution_date"]` and the `{{ execution_date }}` Jinja template.
   Both fail only **when the task runs** (proof `airflow/03`). Removing
   metadata-DB access from tasks, SubDAGs → TaskGroups, SLAs → deadline
   alerts, and REST API v1 → v2 all need context.
   - **Plan:** run ruff for the deterministic half, then the LLM for the rest.
4. **Big and breaking.** 315 of 628 public modules no longer import, 95
   `BaseOperator`/`DAG` members are gone, and 31 parameters were removed. All
   3 proofs break. An estimated **~68k repos** still pass `schedule_interval=`
   and **~11.6k** still use 1.x-era operator paths.
5. **Verifiable.** DAG parsing (`DagBag`) plus `dag.test()` run locally
   without a cluster. That's a better test gate than notebooks (pandas) or
   live LLM calls (LangChain).

**Risks:**
- Astronomer could build this itself. That makes them a partner/acquirer
  target as much as a competitor.
- Deterministic import fixes are commoditised by ruff.
- Real DAG repos often have no tests beyond import, so the gate has to
  include `DagBag` parsing and `dag.test()` by default.

**Runner-ups:**
- **LangChain:** pick it if the go-to-market is developer self-serve rather
  than enterprise.
- **pandas 3:** the largest and most dangerous (silent) migration, a strong
  open-source wedge, but hard to monetise.

## 1. Code-level breaks

**Removal counts** come from `probe_old.py` + `resolve_new.py`, which try
every old public module, name, key-class member and parameter on the new
version. "Broken" means it errors, not just that it warns. Raw name diffs
(`diff_surface.py`) are noisier, because of lazy `__getattr__` exports and
`typing` re-exports.

| Library | Top-level names broken | Public modules broken | Key-class members broken | Params removed | Proofs breaking |
|---|---|---|---|---|---|
| Airflow 2.11.2 → 3.3.2 | — | **315 / 628** | **95** (`BaseOperator`, `DAG`) | 31 | 3/3 |
| LangChain 0.3.30 → 1.4.7 | — | **1,313 / 1,320** | — | — | 2/2 |
| LlamaIndex 0.9.48 → 0.14.25 | 48 / 48 | **720 / 730** | — | — | 2/2 |
| Pydantic 1.10.26 → 2.14.1 | **112 / 178** | 1 / 48 | 1 (`BaseModel.Config`) | 0 | 3/3 |
| SQLAlchemy 1.4.54 → 2.1.6 | 2 / 123 | 21 / 193 | **35** (`Engine.execute`, `Query.from_self`, `Row.keys` …) | **57** (`bind=` …) | 3/3 |
| NumPy 1.26.4 → 2.5.4 | **58 / 401** | 29 / 93 | 4 (`ndarray.ptp`, `itemset` …) | 3 | 2/2 |
| pandas 2.3.3 → 3.1.0 | 2 / 106 | 9 / 227 | **29** (`applymap`, `pad`, `swapaxes` …) | **78** (`axis=` …) | 3/3 |
| marshmallow 3.26.2 → 4.3.1 | 1 / 11 | 2 / 13 | 4 (`Field.missing`, `context` …) | 9 (`pass_many` …) | 2/2 |

**Proofs:** `results/matrix.txt`, 20 of 20 break. Each was run on the old and
the new version.

| Library | Break | Old | New |
|---|---|---|---|
| Airflow | `DAG(schedule_interval=…)` | works | `TypeError` |
| Airflow | `airflow.operators.python_operator` | works | `ModuleNotFoundError` |
| Airflow | task context `execution_date` | present | gone (fails at **task run**) |
| LangChain | `from langchain.chains import LLMChain` | works | `ModuleNotFoundError` |
| LangChain | `initialize_agent` / `AgentType` | works | `ImportError` (API redesigned) |
| LlamaIndex | `from llama_index import VectorStoreIndex` | works | `ImportError` |
| LlamaIndex | `ServiceContext.from_defaults()` | works | raises, points at the migration guide |
| Pydantic | `from pydantic import BaseSettings` | works | `PydanticImportError` |
| Pydantic | `x: Optional[str]` with no default | `None` | required, so `ValidationError` |
| Pydantic | `code: str` given `123` | `"123"` | `ValidationError` |
| SQLAlchemy | `engine.execute(...)` | works | `AttributeError` |
| SQLAlchemy | **DML on `engine.connect()`** | **2 rows persisted** | **0 rows; silently rolled back** |
| SQLAlchemy | `select([cols])` / raw SQL strings | works | `ArgumentError` |
| NumPy | `np.float_`, `np.NaN`, `np.product` | works | `AttributeError` |
| NumPy | **NEP 50: `np.uint8(200) + 100`** | **300 (int64)** | **44 (uint8)**; `float32 + 3.0` stays float32 |
| pandas | **chained assignment `df["a"][0] = 99`** | **writes** | **silently ignored (Copy-on-Write)** |
| pandas | **`s.dtype == object` on strings** | `True` | **`False` (new `str` dtype)** |
| pandas | `applymap`, `fillna(method=)`, `freq="H"` | works | `AttributeError` / `TypeError` |
| marshmallow | `fields.String(missing=…)` | works | `TypeError` |
| marshmallow | `Schema(context=…)` | works | `TypeError` |

Bold rows are **silent**: no error, a different result. Those are the
breaks only context-aware fixing plus a test gate can catch.

## 2. Stuck users (GitHub code search)

These count usages of **patterns proven to break**, not version pins.
Code search tokenises `pydantic<2` loosely, and `>=1.x` pins also allow the
new major, so pins would over-count. Method:
- Every hit's matched fragment is re-checked for the exact, case-sensitive
  pattern. All queries were ~100% precise on their first page.
- Hits inside copies of the library (forks, vendored, `site-packages`) are
  dropped.
- Repos ≈ files × distinct-repo share in the first 100 hits.

These are orders of magnitude, not counts (`results/search.json`).

| Library | Strongest old-pattern query | ~Repos | Second query | ~Repos |
|---|---|---|---|---|
| pandas | `fillna(method=` | **157k** | `.applymap(` | 66k |
| NumPy | `np.float_` | **132k** | `np.product(` | 45k |
| Airflow | `schedule_interval=` (with `from airflow`) | **68k** | `airflow.operators.python_operator` | 12k |
| LangChain | `from langchain.chains import LLMChain` | 34k | `initialize_agent` | 20k |
| Pydantic | `@root_validator` | 32k | `from pydantic import BaseSettings` | 18k |
| SQLAlchemy | `engine.execute(` | 21k | `select([` | 13k |
| LlamaIndex | `ServiceContext.from_defaults` | 6k | `from llama_index import` | 6k |
| marshmallow | `missing=` (with marshmallow) | 1.6k | `self.context[` | 0.3k |

`np.NaN` was dropped as a query. Search is case-insensitive, so it matched
the valid `np.nan` (9.4M "hits").

## 3. Existing tools: what they leave behind

Each tool ran on a small file of proven-broken old code
(`codemod-samples/`), and the output ran on the new version
(`results/codemods/<lib>/`).

| Library | Tool | Maintained? | Fixed | Left broken / got wrong | Result on new version |
|---|---|---|---|---|---|
| Airflow | ruff `AIR301/302/311/312` (officially recommended) | yes | imports → `airflow.sdk` / `providers.standard`, `schedule_interval` → `schedule` | `context["execution_date"]`, `{{ execution_date }}` template, DB access, SubDAG, SLA | DAG **loads**, task **fails at run** |
| LangChain | `langchain-cli migrate` (official) | last release 2025-08-30, before 1.0 | nothing | **crashes**: its Grit download URL 404s (repo moved to `biomejs/gritql`); its rules are 0.2/0.3-era only (no `langchain_classic` or `create_agent` rules) | `ImportError` |
| LangChain | Moderne `UpgradeToLangChain1` (OpenRewrite) | paid (Moderne customers) | per its docs: v0.2 moves + `langchain_classic` imports | not tested (paid) | — |
| LlamaIndex | `llamaindex-cli upgrade-file` (official) | **deprecated**: "no longer maintained"; crashes on current core | (0.10-era CLI) imports → `llama_index.core` | `ServiceContext` left | raises (ServiceContext) |
| Pydantic | `bump-pydantic` (official) | last release **2023-12-28** | `BaseSettings` → `pydantic_settings`, `Optional = None`, `regex` → `pattern`, `@validator` → `@field_validator`, `Config` → `model_config` | `@root_validator` untouched; **gave a `BaseModel` a `SettingsConfigDict`** (wrong); can't know the code relied on `123` → `"123"` | error at class creation |
| NumPy | ruff `NPY201` | yes | `np.float_`, `np.NaN`, `np.product`, `np.alltrue`, `np.cumproduct` | `np.in1d` (removed after 2.0); NEP 50 dtype/value changes; C-ABI rebuilds | `AttributeError: in1d` |
| SQLAlchemy | none official | — | — | vendor AI case study (Codegen) and academic LLM papers only | — |
| pandas | none (ruff `PD` rules are style lints) | — | — | official advice: upgrade to 2.3 and read the warnings | — |
| marshmallow | `upgrade-marshmallow` 0.1.4 | last release 2021-12, predates v4 | — | — | — |

## 4. LLM share

These are the notable breaks for each library, from its official migration
guide and confirmed by the proofs and tool runs above. Each break is one of:
- **D**: a 1:1 rename or move that libcst can rewrite;
- **L**: needs context from the surrounding code;
- **U**: can't be fixed in code (ops, ABI, or a human decision).

| Library | D | L | U | **LLM share** | What the L items are |
|---|---|---|---|---|---|
| Airflow | 3 | 6 | 1 | **60%** | context keys (`execution_date`, `next_ds`, `prev_ds` …) in code **and templates**; DB access → Task SDK / REST; SubDAG → TaskGroup; SLA → deadline alerts; REST v1 → v2; changed defaults (e.g. `catchup`) |
| SQLAlchemy | 4 | 6 | 0 | **60%** | connection-less `execute` → connection scoping; **autocommit → explicit `commit()`** (silent); `bind=` removal; `Session(autocommit=True)`; `Query.from_self`; optional `Query` → `select()` |
| pandas | 5 | 5 | 0 | **50%**, all silent | **CoW chained assignment**; Series-as-view mutation; **`str` dtype checks**; `axis=` removal on groupby; datetime resolution |
| LlamaIndex | 3 | 3 | 0 | **50%** | `ServiceContext` → `Settings`; `LLMPredictor`; agents/workflows API |
| Pydantic | 7 | 5 | 0 | **42%** | `@root_validator` → `@model_validator`; validator bodies using `values` / `field`; stricter coercion; `__root__` → `RootModel`; custom types / `json_encoders` |
| LangChain | 6 | 4 | 0 | **40%**, but the core of most apps | `initialize_agent` / `AgentExecutor` → `create_agent`; memory → LangGraph checkpointer; callbacks / tracing; `LLMChain` → LCEL (or a deterministic move to `langchain_classic`) |
| NumPy | 4 | 3 | 1 | **37%** | NEP 50 promotion; `copy=False` semantics; Windows default int (U: ABI rebuilds) |
| marshmallow | 4 | 2 | 0 | **33%** | `context` → contextvars; base `Field` changes |

Our marginal value is the **L** column minus what tools already cover:
- **Airflow and NumPy:** ruff already does most of the D column.
- **Pydantic:** bump-pydantic does part of it.
- **SQLAlchemy and pandas:** both columns are wide open.

## 5. Who would pay

| Library | Owner | Business at stake | Score |
|---|---|---|---|
| **Airflow** | Apache project; **Astronomer** (Astro), AWS MWAA, Google Cloud Composer | Astronomer: $93M Series D (2025-05), ARR +150% YoY, NRR 130%; Airflow 2 on Astro supported only to **2027-04**. Managed services must move customers or lose them | 5 |
| LangChain | LangChain Inc | $125M Series B at $1.25B (2025-10); LangSmith ARR est. $12–16M; upgrade friction pushes users to other agent frameworks | 4 |
| Pydantic | Pydantic Inc | $12.5M Series A (Sequoia, 2024-10) for Logfire; Pydantic AI and Logfire assume v2 | 3 |
| LlamaIndex | LlamaIndex Inc | $19M Series A (2025-03), $27.5M total; LlamaCloud | 3 |
| SQLAlchemy | volunteer-led, no company | the payer would be enterprise app teams directly | 2 |
| pandas | NumFOCUS-sponsored, no company | the payer would be enterprise data teams directly; Fedora etc. are absorbing 3.0 downstream | 2 |
| NumPy | NumFOCUS-sponsored, no company | — | 1 |
| marshmallow | volunteer | — | 1 |

## Scoring detail

| Library | Stuck users (15) | Tooling gap (20) | LLM share (15) | Who pays (25) | Verifiable (10) | Urgency (15) | **Total** |
|---|---|---|---|---|---|---|---|
| Airflow | 4 | 3 | 4 | 5 | 4 | 5 | **84** |
| LangChain | 3 | 5 | 3 | 4 | 2 | 3 | **71** |
| pandas | 5 | 5 | 4 | 2 | 2 | 3 | **70** |
| SQLAlchemy | 3 | 5 | 4 | 2 | 3 | 2 | **63** |
| Pydantic | 3 | 3 | 3 | 3 | 5 | 2 | **61** |
| LlamaIndex | 2 | 4 | 3 | 3 | 2 | 2 | **56** |
| NumPy | 5 | 1 | 2 | 1 | 4 | 2 | **44** |
| marshmallow | 1 | 4 | 2 | 1 | 4 | 2 | **44** |

## Reproducing

```bash
./setup_venvs.sh      # 17 uv venvs, Python 3.12 (Airflow pinned with its official constraints)
./run_resolve.sh      # old surface probed, then resolved on the new version -> results/resolve-*.json
python3 run_cases.py > results/matrix.txt                  # proofs (run with any Python)
./run_codemods.sh     # existing tools on codemod-samples/ -> results/codemods/
python3 search.py     # needs `gh auth login`; ~3 min (code-search rate limit)
```

`surface.py` + `diff_surface.py` give the raw (noisier) name diffs in
`results/diff-*.json`. `results/user_facing_summary.json` holds the counts
in section 1.

## Sources

- Airflow lifecycle: [supported versions (3.3.2)](https://airflow.apache.org/docs/apache-airflow/3.3.2/installation/supported-versions.html), [Astronomer: Airflow 2 EOL](https://www.astronomer.io/airflow-2-eol/), [Upgrading to Airflow 3 (ruff AIR rules)](https://airflow.apache.org/docs/apache-airflow/3.3.2/installation/upgrading_to_airflow3.html), [MWAA versions](https://docs.aws.amazon.com/mwaa/latest/userguide/airflow-versions.html), [Astro runtime release feed](https://astronomer.io/docs/runtime/runtime-release-feed)
- Astronomer funding: [BigDATAwire: $93M Series D](https://www.hpcwire.com/bigdatawire/this-just-in/astronomer-secures-93m-series-d-funding-to-deliver-unified-dataops-platform-for-enterprise-ai/)
- LangChain: [v1 migration guide](https://docs.langchain.com/oss/python/migrate/langchain-v1/index.html), [Moderne UpgradeToLangChain1](https://docs.moderne.io/user-documentation/recipes/recipe-catalog/python/migrate/langchain/upgradetolangchain1/), [TechCrunch: unicorn round](https://techcrunch.com/2025/07/08/langchain-is-about-to-become-a-unicorn-sources-say), [Sacra](https://sacra.com/c/langchain)
- pandas: [3.0.0 release notes](https://pandas.pydata.org/docs/whatsnew/v3.0.0.html), [migration guide](https://pandas.pydata.org/docs/dev/user_guide/migration.html), [Fedora Pandas3 change](https://fedoraproject.org/wiki/Changes/Pandas3)
- SQLAlchemy: [Codegen AI SQLAlchemy upgrade case study](https://codegen.com/blog/case-study-ai-powered-sqlalchemy-upgrade), [ESEM 2024: LLM library migration](https://conf.researchr.org/details/esem-2024/esem-2024-emerging-results-vision-and-reflection-papers/6/Automatic-Library-Migration-Using-Large-Language-Models-First-Results)
- Pydantic: [Logfire + Series A announcement](https://pydantic.dev/articles/logfire-announcement), [TechCrunch](https://techcrunch.com/2024/10/01/sequoia-backs-pydantic-to-expand-beyond-its-open-source-data-validation-framework)
- LlamaIndex: [Series A + LlamaCloud GA](https://www.llamaindex.ai/blog/announcing-our-series-a-and-llamacloud-general-availability)
- marshmallow: [marshmallow 4 released](https://opencollective.com/marshmallow/updates/mashmallow-4-released)
- Release dates and tool versions: PyPI JSON API, queried 2026-10-22.
