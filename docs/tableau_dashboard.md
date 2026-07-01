# Tableau Public Dashboard — Build Guide

This dashboard visualizes a real agent run's metadata. The data is produced by
`scripts/export_results.py`, which reads `outputs/analysis_<run_id>/run_metadata.json`
and writes three CSVs into `tableau/`. **No values are hand-entered — every figure
comes from the JSON the agent wrote during its run.**

## 1. Generate the data

```bash
# after a live run of src/agent.py has completed
python3 scripts/export_results.py            # uses the latest run
# or: python3 scripts/export_results.py --run-id <run_id>
```

This writes:

| File | Grain | Key columns |
|---|---|---|
| `tableau/hypothesis_outcomes.csv` | one row per hypothesis | `hypothesis_id`, `statement`, `outcome` (passed/aborted), `test_name`, `p_value`, `significant`, `retry_count`, `self_corrected` |
| `tableau/correction_triggers.csv` | one row per self-correction attempt | `hypothesis_id`, `attempt`, `failure_category`, `failure_mode` |
| `tableau/failure_mode_summary.csv` | one row per failure mode | `failure_category`, `failure_mode`, `trigger_count` |

## 2. Connect in Tableau Public

1. Open **Tableau Public** (free desktop app) → **Connect → Text file** → select
   `tableau/hypothesis_outcomes.csv`.
2. Add the other two CSVs as separate data sources (Data → New Data Source). They
   share `run_id` and `hypothesis_id` if you want to relate them, but each view
   below uses a single source, so relating is optional.

## 3. Build the three required views

**View A — Hypothesis pass/fail breakdown** (source: `hypothesis_outcomes`)
- Columns: `outcome`; Rows: `COUNT(hypothesis_id)`.
- Color by `self_corrected` to show how many passing hypotheses required a retry.
- Bar chart. Title: "Hypothesis outcomes (passed vs aborted)".

**View B — Self-correction triggers by failure mode** (source: `failure_mode_summary`)
- Columns: `SUM(trigger_count)`; Rows: `failure_mode` (sorted descending).
- Color by `failure_category` (safety_violation / execution_error / validation_failure).
- Horizontal bar chart. Title: "Self-correction triggers by failure mode".

**View C — Retries per hypothesis** (source: `hypothesis_outcomes`) — *the third view*
- Columns: `hypothesis_id`; Rows: `retry_count`.
- Color/label by `test_name` and tooltip the `p_value` + `significant`.
- Bar chart. Title: "Self-correction retries and statistical test per hypothesis".

## 4. Assemble and publish

1. New **Dashboard**; drag Views A, B, C onto it. Add a title with the real
   `run_id` (visible in every CSV's `run_id` column).
2. **File → Save to Tableau Public As…**, sign in, publish.
3. Copy the public URL and paste it into `README.md` (the "Interactive Dashboard"
   section) and into the LinkedIn description.

> **Integrity note:** publish only after a real `src/agent.py` run has produced the
> CSVs. Do not populate the workbook with placeholder or hand-typed numbers — the
> whole point of the dashboard is that it renders the actual run metadata.
