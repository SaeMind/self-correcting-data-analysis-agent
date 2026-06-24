# Self-Correcting Data Analysis Agent — Design Specification

**Project:** Self-Correcting Data Analysis Agent  
**Domain:** Clinical Data Science / Healthcare Analytics  
**Version:** 1.0.0  
**Author:** Andrew Lee  
**Stack:** Python 3.11 · Claude API (claude-sonnet-4-6) · SQLite · Pandas · Scipy

---

## 1. Problem Statement

Clinical data science teams spend substantial time writing exploratory SQL queries, debugging failures, and validating outputs manually. A self-correcting agentic system can autonomously generate valid exploratory analyses from healthcare claims data — detecting errors, revising queries, and producing structured reports without human intervention.

**Clinical Question:** Can an AI agent autonomously generate valid exploratory analyses from synthetic healthcare claims data with zero unrecovered errors across five distinct hypotheses?

**Success Criterion:** Agent completes ≥5 analyses with 0 unrecovered failures and generates a structured Markdown + JSON report per run.

---

## 2. System Architecture Overview

```
[CLI Entry] → [DataLoader] → [SchemaInspector]
                                    ↓
                         [HypothesisGenerator]  ← Claude API (reasoning)
                                    ↓
                          [QueryPlanner]         ← Claude API (tool_use)
                                    ↓
                         [SandboxExecutor]
                                    ↓
                         [ResultValidator]
                                    ↓
                    [Pass?] ──YES──→ [ReportWriter]
                       ↓                   ↓
                      NO             [OutputSaver]
                       ↓
                [ErrorCorrector]     ← Claude API (self-correction)
                       ↓
              [Retry ≤3?] ──YES──→ [QueryPlanner]
                       ↓
                      NO
                [AbortHypothesis + Log]
```

---

## 3. Stage-by-Stage Specification

### Stage 1 — Data Introspection

**Trigger:** Agent receives dataset path via CLI.

**Tool:** `schema_inspect(dataset_path: str) → SchemaReport`

**Outputs:**
- Column names, inferred types, row count
- Missing value percentage per column
- Cardinality estimate per categorical column
- Numeric range summary (min, max, mean, std)
- Sample of 3 rows (PII fields hashed at this stage)

**Agent behavior:** Claude reads the SchemaReport and builds an internal representation of what analyses are feasible. Columns with >80% missing are flagged as unreliable and excluded from hypothesis generation.

---

### Stage 2 — Hypothesis Generation

**Trigger:** Schema report received.

**Tool:** None (pure LLM reasoning pass).

**Prompt strategy:** Claude is given the schema summary and instructed to propose 5 ranked analytical hypotheses. Each hypothesis must:
1. Be answerable from available columns
2. Have clinical interpretability (explain why it matters)
3. Be testable with a single SQL query or pandas transformation
4. Include an expected direction (e.g., "higher cost in older members")

**Output format:**
```json
{
  "hypothesis_id": "H1",
  "statement": "Members aged 65+ have 40% higher average claim amount than members under 65.",
  "columns_required": ["member_id", "age", "amount"],
  "test_type": "t-test",
  "priority": 1,
  "clinical_rationale": "Medicare-age population carries higher chronic disease burden driving costs."
}
```

**Ranking criteria:**
1. Clinical relevance (weighted 40%)
2. Data completeness of required columns (weighted 40%)
3. Analytical simplicity (weighted 20%)

---

### Stage 3 — Query Execution

**Trigger:** Hypothesis ranked and selected.

**Tool:** `execute_query(sql: str, dataset_path: str, timeout_sec: int) → QueryResult`

**Query safety rules enforced at tool layer (not LLM layer):**
| Rule | Value |
|------|-------|
| Prohibited keywords | DELETE, DROP, INSERT, UPDATE, TRUNCATE, ALTER |
| Max row return | 1,000,000 |
| Query timeout | 30 seconds |
| Execution environment | SQLite in-process (no external DB access) |

**Claude writes parameterized SQL.** Parameters are injected via Python's `sqlite3` parameterized query interface — no f-string interpolation.

**QueryResult fields:**
- `rows`: list of dicts
- `row_count`: int
- `execution_time_ms`: float
- `columns_returned`: list[str]
- `error`: str | None

---

### Stage 4 — Result Validation

**Trigger:** QueryResult received.

**Tool:** `validate_results(query_result: QueryResult, hypothesis: Hypothesis) → ValidationReport`

**Validation checks (ordered):**

| Check | Threshold | Action on Fail |
|-------|-----------|----------------|
| Null rate per column | >50% → invalid | Retry with different column selection |
| Numeric range (age) | <0 or >130 → invalid | Flag + exclude outliers, retry |
| Numeric range (cost) | <0 → invalid | Filter negatives, retry |
| IQR outlier rate | >30% flagged | Warn, do not abort |
| Z-score outlier rate | >5σ values present | Log count, include in caveat section |
| Row count | 0 rows → invalid | Abort hypothesis (no data) |
| Statistical significance | p < 0.05 for group comparisons | Note in report; do not abort on p ≥ 0.05 |
| Diagnosis code present | NULL diagnosis > 10% of claims | Flag as data quality caveat |

**ValidationReport fields:**
- `passed`: bool
- `failure_reason`: str | None
- `warnings`: list[str]
- `statistical_result`: dict (test name, statistic, p-value, effect size)

---

### Stage 5 — Self-Correction

**Trigger:** ValidationReport with `passed = False`.

**Max retries:** 3 per hypothesis. Retry count tracked in agent state.

**Correction strategy:**

| Failure Type | Correction Action |
|---|---|
| SQL syntax error | Claude rewrites query with error message as context |
| Zero rows returned | Claude relaxes filter conditions or broadens date range |
| Negative cost values | Claude adds `WHERE amount >= 0` filter |
| Invalid age values | Claude adds `WHERE age BETWEEN 0 AND 130` filter |
| Timeout | Claude simplifies query (remove joins, add LIMIT) |
| Null threshold exceeded | Claude switches to a different column in the hypothesis |

**After 3 failed retries:** Hypothesis is aborted. Agent logs the failure, records the error chain, and advances to the next hypothesis. A minimum of 3 successful analyses must complete for the run to be considered successful.

---

### Stage 6 — Report Generation

**Trigger:** All hypotheses processed (passed or aborted).

**Tool:** `save_report(analyses: list[Analysis], metadata: RunMetadata) → str`

**Output artifacts per run:**

| File | Format | Contents |
|------|--------|----------|
| `analysis_report_<timestamp>.md` | Markdown | One section per hypothesis: question, method, result, interpretation, caveats |
| `run_metadata_<timestamp>.json` | JSON | Hypothesis list, retry counts, execution times, error chains, model version |
| `figures/` | PNG | One chart per successful analysis (bar, histogram, or scatter) |

**PII handling in all outputs:** `member_id` and `provider_id` are SHA-256 hashed before any output is written. Raw IDs never appear in reports or logs.

---

## 4. State Management

Agent maintains a `RunState` object across all stages:

```python
@dataclass
class RunState:
    dataset_path: str
    schema_report: SchemaReport
    hypotheses: list[Hypothesis]
    query_log: list[QueryLog]          # all queries attempted
    error_log: list[ErrorLog]          # all validation failures
    successful_analyses: list[Analysis]
    retry_counts: dict[str, int]       # hypothesis_id → retry count
    run_id: str
    start_time: datetime
    model_version: str
```

State is serialized to `/outputs/state_<run_id>.json` after each stage for crash recovery.

---

## 5. Safety Guardrails Summary

| Guardrail | Implementation Layer |
|-----------|---------------------|
| No destructive SQL | Tool-layer keyword blocklist |
| Query timeout | `sqlite3` connection timeout param |
| Row cap | LIMIT injected at tool layer if missing |
| PII masking | Pre-output hashing in `save_report` |
| Max retries | Agent state counter, hard-stopped at 3 |
| Medical safety flag | Diagnosis null rate check in validator |
| No external network calls | Sandbox environment, no requests/httpx |
| Model version pinned | Hardcoded to `claude-sonnet-4-6` in config |

---

## 6. Evaluation Protocol

Run agent against 5 test datasets of varying quality:

| Dataset | Characteristics | Expected Outcome |
|---------|----------------|-----------------|
| `clean_10k.csv` | No nulls, valid ranges | All 5 hypotheses pass on first attempt |
| `missing_30pct.csv` | 30% nulls in cost column | 1–2 retries, all pass |
| `negative_costs.csv` | 15% negative amount values | Self-corrects with WHERE filter |
| `outlier_heavy.csv` | 20% IQR outliers | Warnings logged, analyses complete |
| `sparse_dx_codes.csv` | 40% null diagnosis codes | Medical safety flag raised, analyses complete with caveat |

Success metric: ≥90% hypothesis completion rate across all 5 datasets.

---

## 7. Limitations

- Operates on SQLite only; extension to PostgreSQL or BigQuery requires executor refactor
- Hypothesis generation quality depends on schema richness — sparse schemas produce generic hypotheses
- Self-correction is limited to structural errors; domain-incorrect hypotheses are not detected
- No causal inference capability — outputs are descriptive and associative only
- Synthetic data results do not generalize to real claims without validation on credentialed datasets (MIMIC-IV, CMS)

---

## 8. Future Work

- Integrate MIMIC-IV via PhysioNet credentialed access for validation on real EHR data
- Add OMOP CDM compatibility layer for standardized vocabulary mapping
- Extend to longitudinal cohort analysis (time-series hypotheses)
- Implement confidence-weighted hypothesis ranking using retrieval-augmented clinical knowledge
- Deploy as REST API with FastAPI wrapper for team-level access
