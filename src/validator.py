"""
validator.py — Statistical and data-quality validation for query results.

Implements the ordered check sequence defined in docs/agent_design.md Stage 4.
First hard-fail short-circuits the remaining checks. Soft failures are
collected as warnings and do not block analysis completion.

Author: Andrew Lee
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from src import config

logger = logging.getLogger(__name__)


@dataclass
class ValidationReport:
    """Structured result of validate_results()."""

    passed: bool
    failure_reason: str | None
    warnings: list[str] = field(default_factory=list)
    statistical_result: dict[str, Any] | None = None
    checks_run: list[str] = field(default_factory=list)


def validate_results(
    rows: list[dict],
    columns_returned: list[str],
    test_type: str,
    group_column: str | None = None,
    value_column: str | None = None,
) -> ValidationReport:
    """
    Run the full validation sequence against a query result.

    Args:
        rows: Query result rows as list of dicts.
        columns_returned: Column names present in the result set.
        test_type: One of "t-test", "chi-square", "regression", "descriptive".
        group_column: Categorical column used for group comparison tests.
        value_column: Numeric column being tested.

    Returns:
        ValidationReport with pass/fail status, failure reason, warnings,
        and statistical test output.
    """
    checks_run: list[str] = []
    warnings: list[str] = []

    # ── Check 1: zero rows ──
    checks_run.append("row_count_check")
    if len(rows) == 0:
        return ValidationReport(
            passed=False,
            failure_reason="no_rows",
            warnings=warnings,
            statistical_result=None,
            checks_run=checks_run,
        )

    df = pd.DataFrame(rows)

    # ── Check 1b: required columns present (prevents downstream KeyError) ──
    checks_run.append("required_columns_check")
    for required_col in (group_column, value_column):
        if required_col and required_col not in df.columns:
            return ValidationReport(
                passed=False,
                failure_reason=f"missing_column:{required_col}",
                warnings=warnings,
                statistical_result=None,
                checks_run=checks_run,
            )

    # ── Check 2: null rate per column ──
    checks_run.append("null_rate_check")
    for col in df.columns:
        null_pct = df[col].isna().mean()
        if null_pct > config.NULL_RATE_FAIL_THRESHOLD:
            return ValidationReport(
                passed=False,
                failure_reason=f"high_nulls:{col}",
                warnings=warnings,
                statistical_result=None,
                checks_run=checks_run,
            )

    # ── Check 3: invalid age values ──
    checks_run.append("age_range_check")
    if "age" in df.columns:
        numeric_age = pd.to_numeric(df["age"], errors="coerce")
        if ((numeric_age < config.MIN_AGE) | (numeric_age > config.MAX_AGE)).any():
            return ValidationReport(
                passed=False,
                failure_reason="invalid_age",
                warnings=warnings,
                statistical_result=None,
                checks_run=checks_run,
            )

    # ── Check 4: negative cost values ──
    checks_run.append("negative_cost_check")
    if "amount" in df.columns:
        numeric_amount = pd.to_numeric(df["amount"], errors="coerce")
        if (numeric_amount < 0).any():
            return ValidationReport(
                passed=False,
                failure_reason="negative_cost",
                warnings=warnings,
                statistical_result=None,
                checks_run=checks_run,
            )

    # ── Check 5: IQR outlier rate (soft) ──
    checks_run.append("iqr_outlier_check")
    if value_column and value_column in df.columns:
        outlier_rate = _iqr_outlier_rate(df[value_column])
        if outlier_rate > config.IQR_OUTLIER_WARN_THRESHOLD:
            warnings.append(f"iqr_outlier_rate:{outlier_rate:.2f}")

    # ── Check 6: Z-score extreme values (soft) ──
    checks_run.append("zscore_check")
    if value_column and value_column in df.columns:
        z_count = _zscore_extreme_count(df[value_column])
        if z_count > 0:
            warnings.append(f"zscore_gt_{config.ZSCORE_FLAG_THRESHOLD}_count:{z_count}")

    # ── Check 7: diagnosis code completeness (soft, medical safety) ──
    checks_run.append("diagnosis_code_check")
    if "diagnosis_code" in df.columns:
        dx_null_pct = df["diagnosis_code"].isna().mean()
        if dx_null_pct > config.DIAGNOSIS_NULL_WARN_THRESHOLD:
            warnings.append(f"medical_safety_flag:diagnosis_null_rate_{dx_null_pct:.2f}")

    # ── Check 7b: pre-aggregated data detection (chi-square requires raw rows) ──
    checks_run.append("aggregation_check")
    if test_type == "chi-square" and group_column and value_column:
        if group_column in df.columns and value_column in df.columns:
            unique_pairs = df[[group_column, value_column]].drop_duplicates().shape[0]
            if unique_pairs >= len(df):
                return ValidationReport(
                    passed=False,
                    failure_reason=(
                        "aggregated_data_detected:chi_square_requires_raw_"
                        "per_observation_rows_not_grouped_counts"
                    ),
                    warnings=warnings,
                    statistical_result=None,
                    checks_run=checks_run,
                )

    # ── Check 8: statistical test ──
    checks_run.append(f"statistical_test:{test_type}")
    statistical_result = _run_statistical_test(
        df=df,
        test_type=test_type,
        group_column=group_column,
        value_column=value_column,
    )

    return ValidationReport(
        passed=True,
        failure_reason=None,
        warnings=warnings,
        statistical_result=statistical_result,
        checks_run=checks_run,
    )


def _iqr_outlier_rate(series: pd.Series) -> float:
    """Return the fraction of values outside 1.5*IQR of the column."""
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if len(numeric) == 0:
        return 0.0
    q1, q3 = numeric.quantile(0.25), numeric.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return 0.0
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    outliers = numeric[(numeric < lower) | (numeric > upper)]
    return len(outliers) / len(numeric)


def _zscore_extreme_count(series: pd.Series) -> int:
    """Return count of values with |z-score| greater than the configured threshold."""
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if len(numeric) < 2 or numeric.std() == 0:
        return 0
    z_scores = np.abs(stats.zscore(numeric))
    return int((z_scores > config.ZSCORE_FLAG_THRESHOLD).sum())


def _run_statistical_test(
    df: pd.DataFrame,
    test_type: str,
    group_column: str | None,
    value_column: str | None,
) -> dict[str, Any]:
    """
    Dispatch to the appropriate statistical test and return a normalized result dict.
    Falls back to descriptive statistics if required columns are absent.
    """
    if test_type == "t-test" and group_column and value_column:
        return _run_ttest(df, group_column, value_column)
    if test_type == "anova" and group_column and value_column:
        return _run_anova(df, group_column, value_column)
    if test_type == "chi-square" and group_column and value_column:
        return _run_chi_square(df, group_column, value_column)
    if test_type == "regression" and value_column:
        return _run_regression(df, value_column, predictor_column=group_column)
    return _run_descriptive(df, value_column)


def _run_ttest(df: pd.DataFrame, group_column: str, value_column: str) -> dict[str, Any]:
    groups = df[group_column].dropna().unique()
    if len(groups) != 2:
        return {
            "test_name": "t-test",
            "statistic": None,
            "p_value": None,
            "effect_size": None,
            "significant": False,
            "direction": None,
            "note": f"expected 2 groups, found {len(groups)}",
        }
    a = pd.to_numeric(df[df[group_column] == groups[0]][value_column], errors="coerce").dropna()
    b = pd.to_numeric(df[df[group_column] == groups[1]][value_column], errors="coerce").dropna()
    statistic, p_value = stats.ttest_ind(a, b, equal_var=False)
    pooled_std = np.sqrt((a.std() ** 2 + b.std() ** 2) / 2) if (a.std() and b.std()) else 0
    effect_size = (a.mean() - b.mean()) / pooled_std if pooled_std else None
    return {
        "test_name": "t-test",
        "statistic": float(statistic),
        "p_value": float(p_value),
        "effect_size": float(effect_size) if effect_size is not None else None,
        "significant": bool(p_value < config.SIGNIFICANCE_ALPHA),
        "direction": str(groups[0]) if a.mean() > b.mean() else str(groups[1]),
    }


def _run_anova(df: pd.DataFrame, group_column: str, value_column: str) -> dict[str, Any]:
    """One-way ANOVA for comparing a continuous outcome across 3+ categorical groups."""
    groups = df[group_column].dropna().unique()
    group_values = [
        pd.to_numeric(df[df[group_column] == g][value_column], errors="coerce").dropna()
        for g in groups
    ]
    group_values = [g for g in group_values if len(g) > 1]
    if len(group_values) < 2:
        return {
            "test_name": "anova",
            "statistic": None,
            "p_value": None,
            "effect_size": None,
            "significant": False,
            "direction": None,
            "note": f"expected 2+ groups with data, found {len(group_values)}",
        }
    statistic, p_value = stats.f_oneway(*group_values)
    grand_mean = pd.concat(group_values).mean()
    ss_between = sum(len(g) * (g.mean() - grand_mean) ** 2 for g in group_values)
    ss_total = sum(((g - grand_mean) ** 2).sum() for g in group_values)
    eta_squared = float(ss_between / ss_total) if ss_total else None
    top_group = groups[int(np.argmax([g.mean() for g in group_values]))]
    return {
        "test_name": "anova",
        "statistic": float(statistic),
        "p_value": float(p_value),
        "effect_size": eta_squared,
        "significant": bool(p_value < config.SIGNIFICANCE_ALPHA),
        "direction": str(top_group),
        "n_groups": len(group_values),
    }

def _run_chi_square(df: pd.DataFrame, group_column: str, value_column: str) -> dict[str, Any]:
    contingency = pd.crosstab(df[group_column], df[value_column])
    statistic, p_value, _, _ = stats.chi2_contingency(contingency)
    return {
        "test_name": "chi-square",
        "statistic": float(statistic),
        "p_value": float(p_value),
        "effect_size": None,
        "significant": bool(p_value < config.SIGNIFICANCE_ALPHA),
        "direction": None,
    }


def _run_regression(
    df: pd.DataFrame, value_column: str, predictor_column: str | None = None
) -> dict[str, Any]:
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if value_column not in numeric_cols:
        return _run_descriptive(df, value_column)

    if predictor_column and predictor_column in numeric_cols and predictor_column != value_column:
        predictor = predictor_column
    else:
        candidates = [c for c in numeric_cols if c != value_column]
        if not candidates:
            return _run_descriptive(df, value_column)
        logger.warning(
            "No explicit predictor_column given for regression; guessing '%s' "
            "from column order. Set group_column on the hypothesis to avoid this.",
            candidates[0],
        )
        predictor = candidates[0]

    x = pd.to_numeric(df[predictor], errors="coerce")
    y = pd.to_numeric(df[value_column], errors="coerce")
    mask = x.notna() & y.notna()
    slope, intercept, r_value, p_value, std_err = stats.linregress(x[mask], y[mask])
    return {
        "test_name": "regression",
        "statistic": float(slope),
        "p_value": float(p_value),
        "effect_size": float(r_value ** 2),
        "significant": bool(p_value < config.SIGNIFICANCE_ALPHA),
        "direction": predictor,
    }


def _run_descriptive(df: pd.DataFrame, value_column: str | None) -> dict[str, Any]:
    if not value_column or value_column not in df.columns:
        return {
            "test_name": "descriptive",
            "statistic": None,
            "p_value": None,
            "effect_size": None,
            "significant": False,
            "direction": None,
        }
    numeric = pd.to_numeric(df[value_column], errors="coerce").dropna()
    return {
        "test_name": "descriptive",
        "statistic": float(numeric.mean()) if len(numeric) else None,
        "p_value": None,
        "effect_size": float(numeric.std()) if len(numeric) else None,
        "significant": False,
        "direction": None,
    }
