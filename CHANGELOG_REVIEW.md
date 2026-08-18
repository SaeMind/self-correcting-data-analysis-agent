# Changelog — Review & Tableau/Literature Extension

This document records the review and extension work performed on top of the
existing Self-Correcting Data Analysis Agent. It is written to the project's
integrity standard: every claim below reflects something actually verified,
run, or added — not asserted from the prior README.

## Verified (existing code, re-checked from source)

- **SQL layer is real and already present.** `src/tools.py` implements
  `build_sqlite_db()` and `execute_query()`; the agent generates and executes
  real SQL against SQLite (not pandas). The safety guardrails — prohibited-keyword
  blocklist, automatic row cap, execution timeout, PII hashing — are enforced in
  the tool layer, not delegated to the model. No SQL work was redone.
- **Deterministic test suite passes 7/7.** Ran `python3 tests/test_agent.py`
  under Python 3.12 with `requirements.txt` installed. All seven tests pass,
  matching the README's claim. (The clean-query regression test reports
  `p ≈ 0.0334`; the README's `0.0335` is a rounding of the same value.)
- **Model configuration is valid.** `src/config.py` defaults `AGENT_MODEL` to
  `claude-sonnet-4-6`, a currently-active Anthropic model ID; the live run will
  not fail on the model string. The agent's `messages.create` calls use only
  parameters valid for that model.

## Added

- **`scripts/export_results.py`** — standard-library-only exporter that reads a
  run's `run_metadata.json` and writes three tidy CSVs to `tableau/`
  (`hypothesis_outcomes`, `correction_triggers`, `failure_mode_summary`). All
  values are pulled directly from the JSON; nothing is computed beyond
  grouping/counting. Verified to run against existing run metadata.
- **`docs/literature_review.md`** — a 1,600-word review of agentic and
  self-correcting AI systems and automated/biomedical hypothesis generation,
  with **12 sources, each individually located by search and bibliographically
  verified** (title/authors/year/venue) against a primary index. Includes the
  critical counterpoint (Huang et al., 2024) that bounds the project's
  self-correction claims.
- **`docs/tableau_dashboard.md`** — step-by-step build guide for the Tableau
  Public dashboard (three required views), driven entirely by the exported CSVs.

## Hygiene

- Removed `.env.save` (a committed template with no real key) from the working
  tree and git index, and added `.env.save` to `.gitignore`.
- Created a local, gitignored `.env` from `.env.example` for the live run.

## Live run — completed (run `ec1d5144`, Claude Sonnet 4.6)

Ran `python3 -m src.agent --dataset data/claims_sample.csv` end-to-end against a
live API. Real results:

- **3 of 5 hypotheses passed** (H1, H3, H5); **2 aborted** (H2, H4) after
  exhausting the 3-retry budget rather than reporting an invalid query.
- **Self-correction triggered on all 5 hypotheses** — 13 correction attempts in
  total — and resolved 3.
- **Failure modes (13 triggers):** `missing_column` ×4, `negative_cost` ×4,
  `misuse of window function AVG()` (execution error) ×4, `aggregated_data_detected` ×1.
- **Manual query inspection:** all three passing queries were checked by hand and
  confirmed correct (raw per-observation rows, required columns present,
  appropriate statistical test) — not merely non-erroring.

**Reconciliation with prior figures:** the prior README reported run `07d0f881`
as 5/5 hypotheses with self-correction on 4/5. This fresh run produced 3/5 with
2 aborts. The difference is live-model variance: in `ec1d5144` the model
repeatedly emitted a SQLite-invalid `AVG()` window function on H2/H4 that it
could not fix within the retry budget. Both runs demonstrate the validator and
bounded-retry fail-safe working correctly — recovering when a fix is reachable,
aborting cleanly when it is not. The README now reports both runs rather than
overwriting the old numbers.

## Tableau Public — published

- Dashboard published from the run `ec1d5144` CSVs (`tableau/*.csv`) and linked in
  the README:
  https://public.tableau.com/views/Self-CorrectingDataAnalysisAgent/Self-CorrectingDataAnalysis
  (canonical URL verified reachable, HTTP 200).

## Round 2 — Tier 1 + Tier 2 review-driven improvements

A full review of `src/agent.py`, `src/tools.py`, `src/validator.py`, `src/config.py`,
`docs/agent_design.md`, and the actual run output surfaced five findings, all
fixed or explicitly tracked as still-open in this round:

1. **The design doc's own success criterion wasn't being met.** `docs/agent_design.md`
   states "≥5 analyses with 0 unrecovered failures"; run `ec1d5144` got 3/5. Root
   cause diagnosed and fixed below (see "Prompt fix").
2. **`figures/` was created but never populated**, despite the design doc
   describing PNG charts per analysis. Fixed below (see "Chart generation").
   Crash-recovery state serialization (also specified, also missing) remains
   unimplemented — added as a tracked limitation in the README.
3. **No multiple-comparisons correction** across the 5 hypotheses tested per run
   at an uncorrected α=0.05. Fixed below.
4. **Single run reported as representative**, despite the design doc's own
   Evaluation Protocol specifying repeated evaluation. Addressed by running the
   fixed system 5 times and reporting every run (see "5-run characterization").
   The design doc's specific protocol — 5 *datasets* of varying data-quality
   profiles, not 5 runs of the same dataset — remains unimplemented and is
   tracked as an open limitation.
5. **Diagnosed root cause of the `ec1d5144` aborts:** the first-attempt query
   planner prompt never forbade aggregate/window functions — only the retry
   corrector prompt did, and even that phrasing didn't rule out
   `AVG(x) OVER (...)`-style misuse. Fixed below.

### Prompt fix

Both `call_query_planner` and `call_error_corrector` in `src/agent.py` now
explicitly forbid `GROUP BY`, aggregate functions, and window functions
(naming the exact `AVG(x) OVER (...)` anti-pattern observed in `ec1d5144`),
since `validator.py`'s statistical tests are computed from raw rows regardless
of test type — SQL-side aggregation was never actually needed.

### Benjamini-Hochberg (FDR) multiple-comparisons correction

Added `validator.apply_multiple_comparison_correction()` — a from-scratch
implementation of the standard BH procedure (no new dependency), applied once
per run across all successful analyses' p-values, adding `p_value_adjusted`
and `significant_adjusted` to each. Verified against a hand-computed example
(p = [0.01, 0.02, 0.03] → all adjusted to 0.03) in the deterministic test
suite, and cross-checked against real run output (see README's "Statistical
rigor" section for the worked example from run `1a561eec`).

### Model upgrade: claude-sonnet-4-6 → claude-sonnet-5

`src/config.py`'s default `AGENT_MODEL` changed to `claude-sonnet-5`;
`thinking={"type": "adaptive"}` and `output_config={"effort": "high"}` added
to all three `messages.create` call sites; `MAX_TOKENS_HYPOTHESIS/QUERY/CORRECTION`
raised (2000/1000/1000 → 8000/4000/4000) to leave headroom for thinking tokens,
which count against `max_tokens`. This also required fixing response parsing:
Sonnet 5 can prepend a `thinking` content block before the `text` block, so the
existing `response.content[0].text` would raise `AttributeError` on a
`ThinkingBlock` — replaced with `_extract_text()`, which scans for the first
`type == "text"` block instead of assuming position 0.

### Chart generation

Added `tools.generate_chart()` (matplotlib, non-interactive `Agg` backend) —
renders a PNG per successful hypothesis (bar+error-bars for t-test/ANOVA,
grouped bar for chi-square, scatter+fit line for regression, histogram for
descriptive), wired into `run_agent()`'s main loop right after validation
passes, and embedded in the Markdown report. Wrapped in try/except so a
plotting failure can never abort a run. Verified deterministically (a new
test asserts a real PNG is written) and against real output — all 4 chart
types were exercised across the 5-run live sample below, and visually
inspected (bar charts render correct group structure, the regression scatter
shows the real age/cost relationship, matching the synthetic dataset's known
cost-generation logic in `data/generate_synthetic_data.py`).

### 5-run characterization (post-fix)

Ran `python -m src.agent --dataset data/claims_sample.csv` 5 times against the
fully updated code. Real, unfiltered results — no run excluded:

| Run | Passed | Aborted | Self-correction triggers | Window-function errors |
|---|---|---|---|---|
| `1a561eec` | 5/5 | 0 | 3 | 0 |
| `af1481bd` | 5/5 | 0 | 3 | 0 |
| `d944159b` | 5/5 | 0 | 3 | 0 |
| `36ea8b07` | 5/5 | 0 | 3 | 0 |
| `dbdbd8d7` | 5/5 | 0 | 3 | 0 |
| **Total** | **25/25** | **0** | **15** | **0** |

All 15 correction triggers were `negative_cost`, all resolved on the first
retry. The window-function failure mode that caused `ec1d5144`'s aborts did
not recur once across the sample (confirmed by grepping all 5 raw run logs
for "window", zero matches, in addition to the structured `error_log` check).

### Test suite

`tests/test_agent.py` extended with `test_generate_chart_writes_png` and
`test_apply_multiple_comparison_correction`. Full suite: **9/9 pass**
(`python3 tests/test_agent.py`, no API key required).

### Doc reconciliation

Fixed stale `claude-sonnet-4-6` references in `docs/agent_design.md`,
`docs/pseudocode.py`, and `docs/tool_definitions.json`. Added the raw-row-only
SQL constraint and a new "Stage 5.5 — Multiple-Comparisons Correction" section
to `docs/agent_design.md`, and updated `docs/workflow_diagram.mermaid` to
include the same step and chart generation — so these documents describe the
system as it now actually behaves, not as it behaved before this round.

### Pending (owner action)

- **Tableau dashboard is stale.** It's still built from the pre-fix `ec1d5144`
  CSVs (3/5 passed) and hasn't been re-published against the current 5-run,
  25/25 state. Regenerating `tableau/*.csv` from a current run and
  re-publishing per `docs/tableau_dashboard.md` is a straightforward follow-up.
- **Design doc's 5-dataset evaluation protocol** (varying data-quality
  profiles, not just repeated runs on one dataset) remains unimplemented.
- **Crash-recovery state serialization** (specified in `docs/agent_design.md`,
  never implemented) remains unimplemented.
