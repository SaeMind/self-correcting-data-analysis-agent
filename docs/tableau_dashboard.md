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

---

## Beginner walkthrough (no prior Tableau experience)

The steps above assume some familiarity with Tableau. This section is the fully
click-by-click version. The data is already generated — you only build visuals
and publish.

### Where the files are
```
tableau/
├── hypothesis_outcomes.csv      (5 rows — one per hypothesis)
├── correction_triggers.csv      (13 rows — one per retry)
└── failure_mode_summary.csv     (4 rows — one per failure mode)
```

### Step 0 — One-time setup
1. Download **Tableau Public** — free, and different from the paid "Tableau
   Desktop": https://www.tableau.com/products/public/download → install.
2. Create a free **Tableau Public account** at https://public.tableau.com. You
   need it to publish and get a shareable link.

### Step 1 — Load the data
1. Open Tableau Public. Left **Connect** pane → **Text file** →
   `tableau/hypothesis_outcomes.csv`.
2. Click the **Sheet 1** tab at the bottom to start charting.
3. To add the other two files later: menu **Data → New Data Source → Text file**
   → pick `correction_triggers.csv`, repeat for `failure_mode_summary.csv`.
   Each sheet below uses only **one** source — select it in the left **Data**
   pane before building that sheet.

> Tableau files text columns under **Dimensions** (blue) and numbers under
> **Measures** (green). That's expected.

### Step 2 — Sheet A: hypothesis pass/fail  *(source: `hypothesis_outcomes`)*
1. Drag **Outcome** → **Columns** shelf.
2. Drag **Hypothesis Id** → **Rows** → right-click the pill → **Measure → Count**
   (bars: passed = 3, aborted = 2).
3. Drag **Self Corrected** → the **Color** box on the **Marks** card.
4. Double-click the sheet-tab name → rename to **Hypothesis outcomes**.

### Step 3 — Sheet B: triggers by failure mode  *(source: `failure_mode_summary`)*
1. New sheet (the "New Worksheet" icon on the bottom bar). Select
   `failure_mode_summary` in the left Data pane first.
2. Drag **Failure Mode** → **Rows**.
3. Drag **Trigger Count** → **Columns** (reads as SUM — fine; one row per mode).
4. Drag **Failure Category** → **Color** on the Marks card.
5. Right-click the **Failure Mode** axis → **Sort → Descending** by Trigger Count.
6. Rename → **Self-correction triggers by failure mode**.

### Step 4 — Sheet C: retries per hypothesis  *(source: `hypothesis_outcomes`)*
1. New sheet.
2. Drag **Hypothesis Id** → **Columns**.
3. Drag **Retry Count** → **Rows** → right-click pill → **Measure → Sum**.
4. Drag **Test Name** → **Color**.
5. Drag **P Value** → the **Tooltip** box (so hover shows the stat result).
6. Rename → **Retries and test per hypothesis**.

### Step 5 — Assemble the dashboard
1. Bottom bar → **New Dashboard** icon (grid icon).
2. Drag the three sheets from the left **Sheets** list onto the canvas.
3. Double-click near the top to add a **Text** title, e.g.
   `Self-Correcting Data Analysis Agent — Run ec1d5144`.

### Step 6 — Publish and get the link
1. Menu **File → Save to Tableau Public As…**
2. Sign in with your Tableau Public account.
3. Name it → **Save**. It uploads and opens in the browser; the page URL (or the
   **Share** button) is your public link.

### Common snags
- *One giant bar / wrong number* → the count/sum aggregation is off: right-click
  the measure pill → **Measure → Count** (outcomes) or **Sum** (retries/triggers).
- *Fields from the wrong file* → select the correct source in the top-left **Data**
  pane before building the sheet.
- *No "Save to Tableau Public" option* → you opened paid Tableau Desktop, not
  **Tableau Public**; install the Public version.

### Published dashboard
This project's live dashboard:
https://public.tableau.com/views/Self-CorrectingDataAnalysisAgent/Self-CorrectingDataAnalysis
