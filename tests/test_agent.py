"""
test_agent.py — Tests for the deterministic tool layer (no live API calls).

Covers schema_inspect, build_sqlite_db, execute_query, validate_results,
generate_chart, apply_multiple_comparison_correction, and save_report against
the generated synthetic dataset. The hypothesis generation, query planning,
and error correction stages require a live ANTHROPIC_API_KEY and are
intentionally excluded from this suite — they are exercised by running
src/agent.py directly.

Author: Andrew Lee
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, tools, validator  # noqa: E402

DATASET_PATH = config.DATA_DIR / "claims_sample.csv"


def test_schema_inspect_runs_and_hashes_pii():
    schema = tools.schema_inspect(DATASET_PATH)
    assert schema.row_count == 10_000
    assert "member_id" in schema.pii_columns_hashed
    assert "provider_id" in schema.pii_columns_hashed
    column_names = [c["name"] for c in schema.columns]
    assert "age" in column_names
    assert "amount" in column_names
    print(f"PASS: schema_inspect — {schema.row_count} rows, "
          f"{len(schema.unreliable_columns)} unreliable columns")


def test_build_sqlite_db_and_execute_query():
    db_path = tools.build_sqlite_db(DATASET_PATH)
    result = tools.execute_query("SELECT * FROM claims LIMIT 10", db_path)
    assert result.error is None
    assert result.row_count == 10
    assert "amount" in result.columns_returned
    print(f"PASS: execute_query — {result.row_count} rows in {result.execution_time_ms:.2f}ms")


def test_execute_query_blocks_prohibited_keywords():
    db_path = tools.build_sqlite_db(DATASET_PATH)
    result = tools.execute_query("DROP TABLE claims", db_path)
    assert result.safety_violation is not None
    assert "DROP" in result.safety_violation
    print(f"PASS: safety blocklist — caught {result.safety_violation}")


def test_validate_results_detects_negative_cost():
    db_path = tools.build_sqlite_db(DATASET_PATH)
    result = tools.execute_query("SELECT amount FROM claims WHERE amount < 0 LIMIT 50", db_path)
    report = validator.validate_results(
        rows=result.rows,
        columns_returned=result.columns_returned,
        test_type="descriptive",
        value_column="amount",
    )
    assert report.passed is False
    assert report.failure_reason == "negative_cost"
    print(f"PASS: validator caught negative_cost — {report.failure_reason}")


def test_validate_results_passes_clean_query():
    db_path = tools.build_sqlite_db(DATASET_PATH)
    result = tools.execute_query(
        "SELECT age, amount FROM claims WHERE amount >= 0 AND age IS NOT NULL LIMIT 500", db_path
    )
    report = validator.validate_results(
        rows=result.rows,
        columns_returned=result.columns_returned,
        test_type="regression",
        value_column="amount",
    )
    assert report.passed is True
    assert report.statistical_result is not None
    print(f"PASS: validator accepted clean query — test={report.statistical_result['test_name']}, "
          f"p={report.statistical_result['p_value']}")


def test_ttest_between_service_types():
    db_path = tools.build_sqlite_db(DATASET_PATH)
    result = tools.execute_query(
        "SELECT service_type, amount FROM claims "
        "WHERE service_type IN ('Inpatient', 'Primary Care') AND amount >= 0",
        db_path,
    )
    report = validator.validate_results(
        rows=result.rows,
        columns_returned=result.columns_returned,
        test_type="t-test",
        group_column="service_type",
        value_column="amount",
    )
    assert report.passed is True
    stat = report.statistical_result
    assert stat["test_name"] == "t-test"
    print(f"PASS: t-test Inpatient vs Primary Care — p={stat['p_value']:.6f}, "
          f"significant={stat['significant']}, direction={stat['direction']}")


def test_generate_chart_writes_png():
    db_path = tools.build_sqlite_db(DATASET_PATH)
    result = tools.execute_query(
        "SELECT service_type, amount FROM claims "
        "WHERE service_type IN ('Inpatient', 'Primary Care') AND amount >= 0",
        db_path,
    )
    report = validator.validate_results(
        rows=result.rows,
        columns_returned=result.columns_returned,
        test_type="t-test",
        group_column="service_type",
        value_column="amount",
    )
    figures_dir = config.OUTPUTS_DIR / "test_figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    filename = tools.generate_chart(
        hypothesis_id="H_CHART_TEST",
        rows=result.rows,
        test_type="t-test",
        group_column="service_type",
        value_column="amount",
        statistical_result=report.statistical_result,
        figures_dir=figures_dir,
    )
    assert filename is not None
    chart_path = figures_dir / filename
    assert chart_path.exists()
    assert chart_path.stat().st_size > 0
    print(f"PASS: generate_chart — wrote {chart_path} ({chart_path.stat().st_size} bytes)")


def test_apply_multiple_comparison_correction():
    # Hand-verified BH example: p = [0.01, 0.02, 0.03], m=3.
    # q(i) = p(i)*m/i -> [0.03, 0.03, 0.03]; cumulative min leaves all three
    # at 0.03, all still significant at alpha=0.05.
    fake_analyses = [
        {"hypothesis_id": "A", "statistical_result": {"p_value": 0.01}},
        {"hypothesis_id": "B", "statistical_result": {"p_value": 0.02}},
        {"hypothesis_id": "C", "statistical_result": {"p_value": 0.03}},
        {"hypothesis_id": "D", "statistical_result": {"p_value": None}},  # untouched
    ]
    corrected = validator.apply_multiple_comparison_correction(fake_analyses, alpha=0.05)
    for a in corrected[:3]:
        adj = a["statistical_result"]["p_value_adjusted"]
        assert abs(adj - 0.03) < 1e-9, f"expected 0.03, got {adj}"
        assert a["statistical_result"]["significant_adjusted"] is True
    assert "p_value_adjusted" not in corrected[3]["statistical_result"]
    print("PASS: apply_multiple_comparison_correction — BH adjusted p-values match hand-computed example")


def test_save_report_writes_files():
    fake_analysis = [{
        "hypothesis_id": "H_TEST",
        "statement": "Smoke test analysis",
        "final_sql": "SELECT 1",
        "row_count": 1,
        "statistical_result": {"test_name": "descriptive", "statistic": 1.0, "p_value": None, "significant": False},
        "warnings": [],
        "retry_count": 0,
    }]
    paths = tools.save_report("smoketest", fake_analysis, [], [])
    assert Path(paths["report_path"]).exists()
    assert Path(paths["metadata_path"]).exists()
    print(f"PASS: save_report — wrote {paths['report_path']}")


if __name__ == "__main__":
    tests = [
        test_schema_inspect_runs_and_hashes_pii,
        test_build_sqlite_db_and_execute_query,
        test_execute_query_blocks_prohibited_keywords,
        test_validate_results_detects_negative_cost,
        test_validate_results_passes_clean_query,
        test_ttest_between_service_types,
        test_generate_chart_writes_png,
        test_apply_multiple_comparison_correction,
        test_save_report_writes_files,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL: {test.__name__} — {exc}")
        except Exception as exc:
            failures += 1
            print(f"ERROR: {test.__name__} — {exc}")

    print(f"\n{len(tests) - failures}/{len(tests)} tests passed.")
    sys.exit(1 if failures else 0)
