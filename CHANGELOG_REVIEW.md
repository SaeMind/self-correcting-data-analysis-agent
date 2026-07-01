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

## Pending (require the user's account)

- **Tableau Public publish** — the workbook data (`tableau/*.csv`) is produced
  from run `ec1d5144`; the public URL will be added to the README after the user
  publishes the dashboard following `docs/tableau_dashboard.md`.
