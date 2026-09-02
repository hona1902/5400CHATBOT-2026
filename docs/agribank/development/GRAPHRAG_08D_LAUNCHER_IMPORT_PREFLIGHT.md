# GraphRAG-08D — Full-Run Launcher & Import-Path Fail-Fast Preflight Hardening

**Status:** OFFLINE HARDENING COMPLETE — awaiting independent review sign-off + checkpoint.
**Scope:** eval-only. No production runtime change. No provider traffic. No DB mutation.
No fixture / retry / classifier / concurrency change. Full-run gate remains CLOSED.

This phase eliminates the attempt-#4 launcher defect **before** another provider-backed
full run can start. It is the sibling of GraphRAG-08C: where 08C hardened the *environment/
config* preflight (attempt #3 — unloaded `.env` → unreadable normal-DB baseline), 08D hardens
the *Python import-path* preflight (attempt #4 — repo root absent from `sys.path` → the
top-level `commands` package unimportable).

## 1. Root cause of attempt #4 (verified against the checkout)

`ModuleNotFoundError: No module named 'commands'`, raised inside
`runner08._vector_embed_all` **after all 75 canonical Sources were already created**
(`graphrag_indexed_count=0`, `per_source_attempts={}` — the vector-embed step never ran).

Verified mechanism:

- `commands` is a **top-level repository package** (`commands/__init__.py`), a sibling of
  `open_notebook/`, holding the surreal-commands entrypoints (`embedding_commands`,
  `graphrag_commands`, …).
- `pyproject.toml` declares `[tool.setuptools] package-dir = {"open_notebook" = "open_notebook"}`.
  The editable install therefore exposes **only** `open_notebook` via a package-scoped
  finder (`__editable___open_notebook_1_14_0_finder.py`). The repository root is **not**
  placed on `sys.path` by the install. Confirmed: a bare `uv run python` has the repo root
  absent from `sys.path`; `open_notebook` imports (installed) but `commands` does not.
- `runner08._vector_embed_all` imports the embed command **lazily** — its first statement is
  `from commands.embedding_commands import EmbedSourceInput, embed_source_command`. So the
  failure is only reachable after Source creation, never at a cheap preflight.
- A standalone driver launched as `uv run python <scratchpad_driver>` puts the **script's**
  directory on `sys.path[0]`, not the repository root, so `commands` cannot be resolved.
  (pytest and `uv run uvicorn` / the worker each place the repo root on `sys.path`, which is
  why every offline test and the normal app path import `commands` fine.)

**ROOT_CAUSE_ATTEMPT_4** = *the top-level `commands` package is excluded from the editable
install (`package-dir` maps only `open_notebook`), and the eval full-index path imports it
lazily; a driver whose `sys.path[0]` is not the repository root cannot import it, and the
failure surfaces only mid-run after 75 Sources are created.*

> **Post-fix confirmation (history).** 08D guards the attempt-#4 launcher/import defect — it does
> **not** mean attempt #5 was never run. Full-run **attempt #5** (`13e59a3edbb8`,
> `REAUTHORIZATION_5`) subsequently ran with a corrected launcher, **reached genuine GraphRAG
> indexing** (all 75 Sources attempted — empirically confirming the fix carried a real run past
> the #4 failure point), and then failed on logical Source **S001** at the **TRACK** surface
> (`DocStatus.FAILED`, **NON_RETRYABLE**, `TRACK_TEXT_PRESENT_NO_ALLOWLIST_MATCH`) — a genuine
> GraphRAG **extraction** failure, categorically different from a launcher defect. No DEV/HOLDOUT
> ran; `VALUE_DECISION_MADE=NO`. The open follow-up is the S001 extraction failure (an authorized
> ephemeral, non-persisting S001 forensic re-index — execution not yet evidenced), not the
> launcher path. See `GRAPHRAG_08_FROZEN_STATE_HANDOFF.md` §6 and `CURRENT_PHASE.md`.

## 2. Fix — deterministic resolver + fail-fast import preflight (eval-only)

New module `open_notebook/integrations/graphrag/eval/launcher_preflight08.py`:

- `resolve_repo_root()` — walks up from **this module's own file location**
  (`Path(__file__)`), returning the first ancestor containing the full marker set
  (`pyproject.toml`, `commands/__init__.py`, `open_notebook/__init__.py`). Never consults
  `os.getcwd()`, `sys.path`, or the shell — so it is invariant to how the driver was launched.
- `ensure_repo_root_on_sys_path()` — process-local `sys.path` insert only when the root is not
  already importable. **Never** writes `PYTHONPATH` or any environment variable; never persists.
- `FULL_INDEX_IMPORT_SURFACE` — the **exact** `(module, attrs)` the full-index path needs:
  `("commands.embedding_commands", ("EmbedSourceInput", "embed_source_command"))`. Verifying
  this real surface (not a synthetic `import open_notebook`) is what catches the attempt-#4
  configuration. A test asserts it stays in sync with `runner08._vector_embed_all`'s source.
- `run_launcher_preflight(mutate_sys_path=True)` — resolves the root, self-heals the path
  (process-local), and verifies the surface; returns a content-free `LauncherPreflight`.
- `require_launcher_ready()` — fail-closed gate raising `LauncherImportPathError(reason_code)`.

Content-safe reason codes: `LAUNCHER_IMPORT_PATH_READY`, `LAUNCHER_REPO_ROOT_UNRESOLVED`,
`LAUNCHER_COMMANDS_MODULE_NOT_IMPORTABLE` (the exact attempt-#4 signature),
`LAUNCHER_FULL_INDEX_IMPORT_SURFACE_NOT_IMPORTABLE`, umbrella `LAUNCHER_IMPORT_PATH_INVALID`.
No raw exception text, provider payload, or secret is ever stored.

## 3. Wiring — frozen preflight ordering

`precheck08.run_full_benchmark` now runs, in order (task §8):

```
fixture verification (08)                     [static]
  -> launcher import-path preflight (08D)     [static — resolve + verify commands surface]
  -> normal-DB identity + baseline (08C)      [read-only]
  -> STOP BEFORE sidecar if either fails      [fail-closed, durable telemetry]
  -> sidecar startup (08C diagnostics)
  -> Option-A temp isolation (08A)
  -> model seed / embedding / indexing        [provider + temp-DB]
```

The launcher preflight fails **closed before the normal-DB read, sidecar, isolation, Source
creation, and any provider traffic**, writing the same content-free pre-isolation failure
artifact (`preflight_failure.json`, now carrying `launcher_preflight`). A known launcher defect
can never again be discovered after embedding 75 Sources.

## 4. Verification evidence (offline)

- New: `tests/test_graphrag_08d_launcher.py` — 16 tests: repo-root resolution, cwd
  independence, exact import-surface match to the runner, **attempt-#4 bad-context detection**
  (faithful reproduction: repo-root entries removed from `sys.path` + `commands*` purged from
  `sys.modules` → `ModuleNotFoundError` → preflight not-ready), self-heal, unresolved-root and
  missing-symbol reason codes, fail-closed-on-any-resolver-error, no-PYTHONPATH/env-mutation, the
  fail-closed ordering guarantee (provider=0 / sidecar=0 / source-creation=0 / isolation-not-entered
  before the preflight), content-free telemetry, and the 08C + methodology/retry freeze regressions.
- 08/08A/08B/08C/08D suite: **111 passed**. 08C behaviour unchanged (the launcher preflight is
  transparent when the import environment is valid).
- Full GraphRAG regression (`-k graphrag`, `OPEN_NOTEBOOK_GRAPHRAG_ENABLED=false`):
  **522 passed / 8 skipped / 0 failed**.
- `ruff check` on changed files: clean. Targeted `mypy` on changed eval modules: clean.
- `FIXTURE_INTEGRITY` before == after == `a58a68535c345e18f0263904f818e4e2068a164056408665d8bb9233eceb143d`.

## 5. Decision flags

```
GRAPH_RAG_08D_LAUNCHER_HARDENING              = COMPLETE
ENV_CONFIG_PREFLIGHT                           = PASS   (08C retained, not regressed)
REPO_ROOT_RESOLUTION                           = PASS
IMPORT_PATH_PREFLIGHT                          = PASS
COMMANDS_IMPORT_PREFLIGHT                       = PASS
FULL_INDEX_IMPORT_SURFACE_PREFLIGHT            = PASS
CWD_INDEPENDENCE                                = PASS
ATTEMPT_4_REGRESSION_CAUGHT                     = YES
PROVIDER_BEFORE_PREFLIGHT_POSSIBLE              = NO
SIDECAR_BEFORE_PREFLIGHT_POSSIBLE               = NO
SOURCE_CREATION_BEFORE_PREFLIGHT_POSSIBLE       = NO
GRAPH_RAG_08D_READY_FOR_FULL_RUN_REAUTHORIZATION = YES  (eligibility only; NOT authorization)
```

Retained unchanged: `FULL_EXECUTION_AUTHORIZED=NO`, `VALUE_EVIDENCE_READY=NO`, all
`*_VALUE_EVIDENCED=NOT_RUN`, `STRUCTURED_EVIDENCE_IMPLEMENTATION_READY=NO`,
`QUERY_DATA_EXPOSES_VALID_RANK/SCORE=NO`, `RRF_CANDIDATE_INTERFACE_READY=NO`,
`GRAPH_CANDIDATE_IMPLEMENTATION_READY=NO`. Retry policy / allowlist / classifier / concurrency
/ `MAX_INDEX_ATTEMPTS_PER_SOURCE=2` / `PARTIAL_CORPUS_ALLOWED=NO` / `FULL_INDEX_REQUIRED=75/75`
all unchanged. No migration (count 50). No `.env` edit.

`GRAPH_RAG_08D_READY_FOR_FULL_RUN_REAUTHORIZATION=YES` means only **eligible for a later
explicit operator authorization** — attempt #5 must not run without one.

## 6. Boundaries preserved

Eval-only (`open_notebook/integrations/graphrag/eval/`); production imports no eval code; no
production `query_data` / `query_evidence` / `GraphEvidenceResult`; no vector/Source-lifecycle
semantic change; no migration; no Ask/Chat/frontend change; no fixture edit; no provider traffic;
sidecar stopped; `OPEN_NOTEBOOK_GRAPHRAG_ENABLED=false`. The four historical full-run failures
remain execution history, never retrieval-value evidence.
