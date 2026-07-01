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

## Pending (require external resources)

- **Live end-to-end run** (`src/agent.py`) — requires a real `ANTHROPIC_API_KEY`.
  Real hypothesis pass count, self-correction trigger count, and failure-mode
  tags will be recorded here and in the README once the run completes; the prior
  README figures (run `07d0f881`: 5/5 hypotheses, self-correction on 4/5) will be
  reconciled against the fresh run rather than reused.
- **Tableau Public publish** — the workbook and CSVs are produced locally; the
  public URL will be added to the README after publishing.
