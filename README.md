# Self-Correcting Data Analysis Agent

> ⭐ **Flagship / signature portfolio project.** An end-to-end agentic system that writes and executes real SQL, validates its own results against statistical and clinical checks, and self-corrects on failure — with a published Tableau dashboard and a verified literature review.

## Overview

An agentic system that autonomously generates exploratory analyses from healthcare claims data using Claude with tool use. The agent inspects a dataset's schema, proposes ranked clinical hypotheses, writes and executes SQL queries in a sandboxed environment, validates results against statistical and clinical sanity checks, and self-corrects on failure — up to 3 retries per hypothesis — before producing a structured Markdown + JSON report.

## Clinical Question

Can an AI agent autonomously generate valid exploratory analyses from healthcare claims data without human intervention, while correctly detecting and recovering from data quality errors (negative costs, missing diagnosis codes, invalid ages)?

## How the Agent Works

1. **Schema inspection** — loads the dataset, hashes PII columns (`member_id`, `provider_id`) immediately, profiles null rates, cardinality, and numeric ranges.
2. **Hypothesis generation** — Claude proposes up to 5 ranked clinical hypotheses from the schema profile.
3. **Query execution** — Claude writes parameterized SQL; a sandbox layer blocks destructive operations, caps row count, and enforces a 30-second timeout.
4. **Result validation** — checks null rates, impossible clinical values (negative cost, invalid age), outlier rates, and runs the appropriate statistical test (t-test, chi-square, regression, or descriptive).
5. **Self-correction** — on failure, Claude receives the specific failure reason and rewrites the query. Max 3 retries per hypothesis.
6. **Report generation** — writes a Markdown report and JSON run metadata with full error/retry lineage.

## Architecture

See `docs/workflow_diagram.mermaid` for the full flowchart and `docs/agent_design.md` for the complete design specification.

## How to Run

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your API key
cp .env.example .env
# edit .env and add your ANTHROPIC_API_KEY

# 3. Generate the synthetic dataset (already included, regenerate if needed)
python data/generate_synthetic_data.py

# 4. Run the agent
python src/agent.py --dataset data/claims_sample.csv

# 5. Run the deterministic test suite (no API key required)
python tests/test_agent.py
```

## Sample Output

The deterministic tool layer (schema inspection, query execution, validation, report writing) has been run-verified against the included 10,000-record synthetic dataset:

```
PASS: schema_inspect — 10000 rows, 0 unreliable columns
PASS: execute_query — 10 rows in 0.34ms
PASS: safety blocklist — caught prohibited_keyword:DROP
PASS: validator caught negative_cost — negative_cost
PASS: validator accepted clean query — test=regression, p=0.0335
PASS: t-test Inpatient vs Primary Care — p=0.000000, significant=True, direction=Inpatient
PASS: save_report — wrote outputs/analysis_smoketest/analysis_report.md

7/7 tests passed.
```

Full end-to-end output (hypothesis generation through final report) requires a live `ANTHROPIC_API_KEY` and is produced in `outputs/analysis_<run_id>/` after running `src/agent.py`.

## Key Findings

Two runs are reported below, both against the same 10,000-record synthetic claims dataset. Because the hypothesis-generation, query-planning, and correction stages involve **live model calls, results vary between runs** — both are reported honestly rather than cherry-picking the better one.

### Current run — `ec1d5144` (Claude Sonnet 4.6)

The agent generated 5 hypotheses. The self-correction loop **triggered on all 5** (13 correction attempts in total) and **resolved 3 of them**; 2 hypotheses (H2, H4) exhausted the 3-retry budget and were **aborted rather than reported with a bad query** — the intended fail-safe behavior.

| # | Hypothesis | Test | Outcome | Result |
|---|---|---|---|---|
| H1 | Chronic condition → claim amount | t-test | passed (2 retries) | p=0.94 — no significant cost difference |
| H2 | *(aborted)* | — | aborted after 3 retries | persistent window-function misuse + column/negative-cost errors |
| H3 | Readmission across 8 diagnosis codes | chi-square | passed (2 retries) | p=0.81 — no significant association |
| H4 | *(aborted)* | — | aborted after 3 retries | persistent SQL window-function misuse |
| H5 | Chronic condition → 30-day readmission | chi-square | passed (1 retry) | p=0.26 — no significant association |

**Self-correction triggers by failure mode (this run, 13 total):**

| Failure mode | Category | Count |
|---|---|---|
| `missing_column` | validation failure | 4 |
| `negative_cost` | validation failure | 4 |
| `misuse of window function AVG()` | execution error | 4 |
| `aggregated_data_detected` | validation failure | 1 |

All three passing queries were **manually inspected and confirmed correct** — raw per-observation rows with the required columns and the appropriate statistical test, not merely non-erroring. (For example, H5's single retry was a genuine correction: the first attempt returned pre-aggregated counts, the validator flagged `aggregated_data_detected`, and the rewrite returned raw rows.)

### Prior run — `07d0f881`

An earlier run completed all 5 hypotheses (**5/5**), with self-correction resolving on **4 of 5**. The difference between the two runs is expected and is reported rather than hidden: it reflects live-model variance, and in run `ec1d5144` the model repeatedly emitted a SQLite-invalid `AVG()` window function on H2/H4 that it could not fix within the 3-retry budget. The honest takeaway is that the deterministic validator and the bounded-retry fail-safe behave correctly in **both** cases — recovering when a fix is reachable, and aborting cleanly when it is not, instead of surfacing an invalid analysis.

## Interactive Dashboard (Tableau Public)

A Tableau Public dashboard visualizes the run metadata directly from the exported CSVs (`tableau/`, produced by `scripts/export_results.py`): (a) hypothesis pass/fail breakdown, (b) self-correction triggers by failure mode, and (c) retries and statistical test per hypothesis.

**Dashboard link:** _to be added after publishing_ — build steps in [`docs/tableau_dashboard.md`](docs/tableau_dashboard.md).

> Every figure in the dashboard comes from `outputs/analysis_<run_id>/run_metadata.json`; no values are hand-entered.

## Literature Review

A full review (~1,600 words, **12 sources each individually search-verified** for title/authors/year/venue) situates this project within four research threads — agentic reasoning and tool use, self-correction in LLMs, text-to-SQL, and automated/biomedical hypothesis generation. See [`docs/literature_review.md`](docs/literature_review.md).

Notably, it includes the critical counterpoint — Huang et al. (2024), *"Large Language Models Cannot Self-Correct Reasoning Yet"* — which finds that *intrinsic* self-correction (no external feedback) often fails. This is exactly why this project's correction loop is driven by a **deterministic external validator** (concrete failure codes such as `negative_cost` or `aggregated_data_detected`) rather than by the model's own judgment, and why the self-correction claims are scoped to structural and data-quality errors only.

## Limitations & Future Work

- Operates on SQLite only; PostgreSQL/BigQuery would require an executor refactor
- Self-correction resolves structural and data-quality errors only — it does not detect domain-incorrect hypotheses
- No causal inference; all outputs are descriptive/associative
- Synthetic data results do not generalize to real claims without validation on a credentialed dataset (e.g., MIMIC-IV, CMS public use files)
- Planned: OMOP CDM compatibility layer, longitudinal cohort hypotheses, FastAPI wrapper for team access

## Technologies Used

Python 3.11 · Anthropic Claude API (`claude-sonnet-4-6`, tool use) · SQLite · pandas · scipy · python-dotenv

---

**Author:** Andrew Lee | [GitHub](https://github.com/SaeMind) | [LinkedIn](https://www.linkedin.com/in/agllee/)
