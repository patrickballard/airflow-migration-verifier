# Which openai SDK changes really break? (1.x → 3.36.1)

Research behind api-change-agent's rule choices. A changelog entry only counts
here if a snippet of the old pattern **runs on openai 1.109.1 and errors or
behaves differently on 3.36.1**. Deprecations that still work are dropped.

## Method

1. Read the 2.0.0 and 3.0.0 `⚠ BREAKING CHANGES` sections, every "remove"
   entry since 1.109, and the SDK's `httpx2.md` migration guide.
2. Diff the installed packages, since changelogs miss things
   (`surface.py`, `fields.py`, `diff_surface.py`): every client method and
   parameter, module, type name and pydantic model field, in 1.109.1, 2.54.0
   and 3.36.1. Result: `results/surface_diff.txt`. No method, resource or
   parameter was removed.
3. Prove each candidate (`cases/`, `pt/`) against `mock.py`, a local stand-in
   for the API, in three venvs:
   - clean 1.109.1;
   - clean 3.36.1;
   - 3.36.1 with `httpx` installed (the "I declared httpx myself" case).

   Result: `results/matrix.txt` and `results/mocking_libs.txt`.
4. Estimate public usage with GitHub code search (`search.py`,
   `search/queries.tsv`). Hits that are copies of the SDK itself are dropped.
   `sample_declared.py` checks, for a sample of repos, whether httpx is
   declared.

## Confirmed breaks

| # | Change | Breaks how | Fix type | ~Repos |
|---|---|---|---|---|
| 1 | `httpx` no longer installed (3.0.0) | `import httpx` → `ModuleNotFoundError` unless declared (cases 01, 02, 04, 07) | deterministic: declare it (**rule `declare-httpx`**) | ~15k (30% of ~50k, n=23) |
| 2 | `certifi`, `tqdm`, `distro` no longer installed (3.0.0) | `ModuleNotFoundError` if imported undeclared (07) | deterministic | ≤10k for certifi; much lower in practice |
| 3 | TLS: certifi → OS trust store (3.0.0) | 1.x verifies against certifi's 120 CAs; 3.x uses `truststore` / OpenSSL defaults (19) | unfixable in code | n/a |
| 4 | Legacy CLI removed (2.35.0) | no `openai` command; `python -m openai` fails (08, 18) | llm for `openai api …`; otherwise unfixable | ~1.2k |
| 5 | respx / pytest-httpx stop intercepting (3.0.0) | mocks bypassed, so `APIConnectionError` even with httpx (03, `pt/`) | llm | ~800 |
| 6 | `ComputerTool` repurposed (2.x) | preview tools parse as `ComputerUsePreviewTool`; `isinstance` → False (13, 13b) | llm | ~670, mostly agents-SDK forks |
| 7 | `raw.http_response` is `httpx2.Response` (3.0.0) | `isinstance(..., httpx.Response)` → False (06) | deterministic | ≤600 (upper bound) |
| 8 | `DefaultHttpxClient(transport=httpx.HTTPTransport())` (3.0.0) | `AssertionError` on first request, even with httpx (05) | deterministic | ~90 |
| 9 | `RealtimeSessionClientSecret` removed (2.x) | `ImportError` (11) | llm | ~0 real |
| 10 | `openai.lib._validators` removed (3.14.1) | `ModuleNotFoundError` (09) | unfixable | 2 |

**Dropped (these still work on 3.36.1):**
- `beta.chat.completions.parse/stream`, and `functions=` / `function_call=` (16).
- 2.0.0's tool-output `str | list`: runtime is identical on both versions (14).
- `prompt_cache_key=` and `include=["web_search_call.results"]` (17).
- `ParsedResponse[T]` (15).
- Importing `ResponseInputAudio` (10), and reading `session.client_secret` (12).
- vcrpy cassettes (`pt/test_vcr.py`).
- Python 3.8 support: pip just picks an older release.

Counts are approximate. Code search counts files on default branches.
Repos are estimated from the share of distinct, non-vendored repos in the
first 100 hits.

## Re-running

```bash
./setup_venvs.sh   # venvs under .venvs/ (git-ignored)
./run_all.sh       # surface diff + proof matrix + mocking libs -> results/
python3 search.py  # needs `gh auth login`; ~2 min (code search rate limit)
python3 sample_declared.py 30
```

The 2026-10-22 run used openai 1.109.1, 2.54.0 and 3.36.1, with httpx 0.28.1
and httpx2 2.16.0.
