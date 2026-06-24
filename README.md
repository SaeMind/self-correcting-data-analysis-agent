# Self-Correcting Data Analysis Agent

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

Run `07d0f881` against the 10,000-record synthetic claims dataset completed all 5 hypotheses, with the self-correction loop triggering and resolving on 4 of 5 (1–3 retries each) before producing a valid result.

| Hypothesis | Test | Result | Interpretation |
|---|---|---|---|
| Chronic condition → claim amount | t-test | p=0.94 | No significant cost difference detected |
| Service type → 30-day readmission | chi-square | p=0.53 | No significant association detected |
| Age → claim amount | regression | p=1.55e-25, R²=0.011 | Significant positive relationship — small effect size, but detectable at this sample size |
| Diagnosis code → claim amount | ANOVA (8 groups) | p=0.82 | No significant difference across diagnosis categories |
| Chronic condition → 30-day readmission | chi-square | p=0.26 | No significant association detected |

The single significant finding (age → cost) is consistent with a real but modest age-based cost gradient; the four null results reflect the dataset's actual structure, where readmission and diagnosis code were generated independently of the other variables. The self-correction mechanism recovered from missing-column errors, an aggregated-data validation failure, a SQL window-function misuse error, and a query-syntax error — all without human intervention.

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
