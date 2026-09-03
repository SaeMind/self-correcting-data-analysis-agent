"""
export_results.py — Export a completed agent run's metadata to CSV for Tableau.

Reads outputs/analysis_<run_id>/run_metadata.json (produced by tools.save_report)
and writes three tidy CSVs into tableau/:

  1. hypothesis_outcomes.csv   — one row per hypothesis the agent completed
  2. correction_triggers.csv   — one row per self-correction attempt (error_log)
  3. failure_mode_summary.csv  — trigger counts grouped by failure mode

Pulls every value directly from the JSON run metadata — no numbers are
computed or invented here beyond grouping/counting what the agent recorded.

Standard library only (no API key, no pandas) so it runs anywhere.

Usage:
    python3 scripts/export_results.py                 # latest run
    python3 scripts/export_results.py --run-id abc123 # a specific run

Author: Andrew Lee
"""

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
TABLEAU_DIR = PROJECT_ROOT / "tableau"


def find_run_dir(run_id: str | None) -> Path:
    """Locate the run directory, defaulting to the most recently modified one."""
    if run_id:
        run_dir = OUTPUTS_DIR / f"analysis_{run_id}"
        if not run_dir.is_dir():
            sys.exit(f"ERROR: run directory not found: {run_dir}")
        return run_dir

    candidates = sorted(
        OUTPUTS_DIR.glob("analysis_*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    candidates = [c for c in candidates if (c / "run_metadata.json").exists()]
    if not candidates:
        sys.exit(
            "ERROR: no run with run_metadata.json found under outputs/. "
            "Run 'python3 src/agent.py --dataset data/claims_sample.csv' first."
        )
    return candidates[0]


def classify_failure_mode(reason: str) -> tuple[str, str]:
    """
    Split an error_log 'reason' string into (category, detail).

    Reason strings are written by agent.py as:
      - "safety_violation:prohibited_keyword:DROP"
      - "execution_error:no such column: foo"
      - "validation_failure:negative_cost"
    """
    parts = reason.split(":", 1)
    category = parts[0]
    detail = parts[1] if len(parts) > 1 else ""
    # For validation failures, the leading token of the detail is the tag
    # (e.g. "missing_column:age" -> "missing_column").
    if category == "validation_failure" and detail:
        detail_tag = detail.split(":", 1)[0]
        return category, detail_tag
    return category, detail


def export(run_dir: Path) -> dict:
    metadata = json.loads((run_dir / "run_metadata.json").read_text(encoding="utf-8"))
    TABLEAU_DIR.mkdir(parents=True, exist_ok=True)

    analyses = metadata.get("analyses", [])
    aborted = metadata.get("aborted_hypotheses", [])
    error_log = metadata.get("error_log", [])

    # 1. hypothesis_outcomes.csv
    outcomes_path = TABLEAU_DIR / "hypothesis_outcomes.csv"
    with outcomes_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "run_id", "hypothesis_id", "statement", "outcome",
            "test_name", "statistic", "p_value", "significant",
            "p_value_adjusted", "significant_adjusted",
            "row_count", "retry_count", "self_corrected", "warnings",
        ])
        for a in analyses:
            stat = a.get("statistical_result") or {}
            retry = a.get("retry_count", 0)
            writer.writerow([
                metadata.get("run_id", ""),
                a.get("hypothesis_id", ""),
                a.get("statement", ""),
                "passed",
                stat.get("test_name", ""),
                stat.get("statistic", ""),
                stat.get("p_value", ""),
                stat.get("significant", ""),
                stat.get("p_value_adjusted", ""),
                stat.get("significant_adjusted", ""),
                a.get("row_count", 0),
                retry,
                "yes" if retry and retry > 0 else "no",
                "; ".join(a.get("warnings") or []),
            ])
        for h_id in aborted:
            writer.writerow([
                metadata.get("run_id", ""), h_id, "", "aborted",
                "", "", "", "", "", "", 0, "", "", "",
            ])

    # 2. correction_triggers.csv
    triggers_path = TABLEAU_DIR / "correction_triggers.csv"
    with triggers_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "run_id", "hypothesis_id", "attempt",
            "failure_category", "failure_mode", "raw_reason",
        ])
        for entry in error_log:
            category, detail = classify_failure_mode(entry.get("reason", ""))
            writer.writerow([
                metadata.get("run_id", ""),
                entry.get("hypothesis_id", ""),
                entry.get("attempt", ""),
                category,
                detail,
                entry.get("reason", ""),
            ])

    # 3. failure_mode_summary.csv
    summary: dict[tuple[str, str], int] = {}
    for entry in error_log:
        category, detail = classify_failure_mode(entry.get("reason", ""))
        summary[(category, detail)] = summary.get((category, detail), 0) + 1
    summary_path = TABLEAU_DIR / "failure_mode_summary.csv"
    with summary_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["run_id", "failure_category", "failure_mode", "trigger_count"])
        for (category, detail), count in sorted(summary.items(), key=lambda kv: -kv[1]):
            writer.writerow([metadata.get("run_id", ""), category, detail, count])

    return {
        "run_id": metadata.get("run_id", ""),
        "model_version": metadata.get("model_version", ""),
        "passed": len(analyses),
        "self_corrected": sum(1 for a in analyses if (a.get("retry_count") or 0) > 0),
        "aborted": len(aborted),
        "correction_triggers": len(error_log),
        "failure_modes": summary,
        "paths": [str(outcomes_path), str(triggers_path), str(summary_path)],
    }

def export_model_comparison(run_ids_by_model: dict, output_dir: Path = TABLEAU_DIR) -> dict:
    """
    Aggregates multiple runs by model into comparison CSVs.
    Guards against the .env model_version mislabeling bug: asserts each
    run's recorded model_version matches the key it's grouped under.
    """
    all_outcomes = []
    all_triggers_raw = []
    summary_rows = []
    fdr_example_rows = []

    for expected_model, run_ids in run_ids_by_model.items():
        model_hyp_count = 0
        model_passed = 0
        model_aborted = 0
        model_triggers = 0
        model_window_errors = 0

        for run_id in run_ids:
            run_dir = OUTPUTS_DIR / f"analysis_{run_id}"
            metadata_path = run_dir / "run_metadata.json"
            metadata = json.loads(metadata_path.read_text())

            actual_model = metadata.get("model_version", "")
            assert actual_model == expected_model, (
                f"Model mismatch for run {run_id}: expected {expected_model}, "
                f"got {actual_model}. Refusing to aggregate."
            )

            passed = metadata.get("analyses", [])
            aborted = metadata.get("aborted_hypotheses", [])
            model_hyp_count += len(passed) + len(aborted)
            model_passed += len(passed)
            model_aborted += len(aborted)

            for h in passed:
                sr = h.get("statistical_result", {})
                all_outcomes.append({
                    "model_version": actual_model,
                    "run_id": run_id,
                    "hypothesis_id": h.get("hypothesis_id", ""),
                    "outcome": "passed",
                    "test_name": sr.get("test_name", ""),
                    "p_value": sr.get("p_value", ""),
                    "p_value_adjusted": sr.get("p_value_adjusted", ""),
                    "significant_adjusted": sr.get("significant_adjusted", ""),
                    "retry_count": h.get("retry_count", 0),
                })
                if run_id == "c93d384f":
                    fdr_example_rows.append({
                        "hypothesis_id": h.get("hypothesis_id", ""),
                        "test_name": sr.get("test_name", ""),
                        "p_value_raw": sr.get("p_value", ""),
                        "p_value_adjusted": sr.get("p_value_adjusted", ""),
                        "significant_raw": sr.get("significant", ""),
                        "significant_adjusted": sr.get("significant_adjusted", ""),
                    })

            for h_id in aborted:
                all_outcomes.append({
                    "model_version": actual_model,
                    "run_id": run_id,
                    "hypothesis_id": h_id if isinstance(h_id, str) else h_id.get("hypothesis_id", ""),
                    "outcome": "aborted",
                    "test_name": "",
                    "p_value": "",
                    "p_value_adjusted": "",
                    "significant_adjusted": "",
                    "retry_count": "",
                })

            for err in metadata.get("error_log", []):
                model_triggers += 1
                reason = err.get("reason", "")
                if ":" in reason:
                    category, mode = reason.split(":", 1)
                else:
                    category, mode = "unknown", reason
                if "window" in mode.lower():
                    model_window_errors += 1
                all_triggers_raw.append({
                    "model_version": actual_model,
                    "failure_category": category,
                    "failure_mode": mode,
                })

        summary_rows.append({
            "model_version": expected_model,
            "total_hypotheses": model_hyp_count,
            "passed": model_passed,
            "aborted": model_aborted,
            "pass_rate": round(model_passed / model_hyp_count, 4) if model_hyp_count else 0,
            "total_self_correction_triggers": model_triggers,
            "window_function_errors": model_window_errors,
        })

    failure_agg = {}
    for t in all_triggers_raw:
        key = (t["model_version"], t["failure_category"], t["failure_mode"])
        failure_agg[key] = failure_agg.get(key, 0) + 1
    failure_summary = [
        {"model_version": m, "failure_category": c, "failure_mode": mode, "trigger_count": n}
        for (m, c, mode), n in failure_agg.items()
    ]

    output_dir.mkdir(exist_ok=True)
    _write_csv(output_dir / "model_comparison_summary.csv", summary_rows)
    _write_csv(output_dir / "hypothesis_outcomes_by_model.csv", all_outcomes)
    _write_csv(output_dir / "failure_mode_by_model.csv", failure_summary)
    _write_csv(output_dir / "fdr_correction_example.csv", fdr_example_rows)

    return {"summary": summary_rows}


def _write_csv(path: Path, rows: list) -> None:
    if not rows:
        return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

def main() -> None:
    parser = argparse.ArgumentParser(description="Export agent run metadata to Tableau CSVs.")
    parser.add_argument("--run-id", type=str, default=None, help="Run ID (default: latest).")
    parser.add_argument("--model-comparison", action="store_true",
                         help="Aggregate the 10-run Sonnet 4.6 vs Sonnet 5 comparison")
    args = parser.parse_args()

    if args.model_comparison:
        run_ids_by_model = {
            "claude-sonnet-4-6": ["1a561eec", "af1481bd", "d944159b", "36ea8b07", "dbdbd8d7"],
            "claude-sonnet-5": ["c93d384f", "7b7d8423", "bd48c2a9", "04cbf330", "c063b712"],
        }
        result = export_model_comparison(run_ids_by_model)
        print("Model comparison exported:")
        for row in result["summary"]:
            print(f"  {row['model_version']}: {row['passed']}/{row['total_hypotheses']} passed, "
                  f"{row['total_self_correction_triggers']} triggers, "
                  f"{row['window_function_errors']} window-function errors")
        return

    run_dir = find_run_dir(args.run_id)
    result = export(run_dir)

    print(f"Run:                 {result['run_id']} (model: {result['model_version']})")
    print(f"Hypotheses passed:   {result['passed']}")
    print(f"  self-corrected:    {result['self_corrected']}")
    print(f"Hypotheses aborted:  {result['aborted']}")
    print(f"Correction triggers: {result['correction_triggers']}")
    if result["failure_modes"]:
        print("Failure modes:")
        for (category, detail), count in sorted(result["failure_modes"].items(), key=lambda kv: -kv[1]):
            label = f"{category}:{detail}" if detail else category
            print(f"  {label}: {count}")
    print("Wrote:")
    for p in result["paths"]:
        print(f"  {p}")


if __name__ == "__main__":
    main()
