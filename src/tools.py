"""
tools.py — Tool implementations for the Self-Correcting Data Analysis Agent.

Each function here corresponds to a tool definition in docs/tool_definitions.json.
Safety guardrails (SQL keyword blocklist, row caps, timeouts, PII hashing) are
enforced in this module, not delegated to the LLM.

Author: Andrew Lee
"""

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # non-interactive backend; safe for headless/server use
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src import config

logger = logging.getLogger(__name__)


class SafetyViolationError(Exception):
    """Raised when a query contains a prohibited operation."""


@dataclass
class SchemaReport:
    """Structured result of schema_inspect()."""

    row_count: int
    columns: list[dict[str, Any]]
    unreliable_columns: list[str]
    pii_columns_hashed: list[str]

    def to_prompt_string(self) -> str:
        """Render a compact text summary suitable for an LLM prompt."""
        lines = [f"Dataset: {self.row_count} rows."]
        for col in self.columns:
            lines.append(
                f"- {col['name']} ({col['dtype']}): "
                f"null_rate={col['null_pct']:.2f}, "
                f"cardinality={col['cardinality']}"
                + (
                    f", range=[{col['min']}, {col['max']}]"
                    if col["dtype"] == "numeric"
                    else ""
                )
            )
        if self.unreliable_columns:
            lines.append(f"Unreliable columns (excluded): {', '.join(self.unreliable_columns)}")
        return "\n".join(lines)


@dataclass
class QueryResult:
    """Structured result of execute_query()."""

    rows: list[dict]
    row_count: int
    execution_time_ms: float
    columns_returned: list[str]
    error: str | None = None
    safety_violation: str | None = None


def hash_pii(value: Any) -> str:
    """SHA-256 hash a PII value with the configured salt."""
    salted = f"{config.HASH_SALT}:{value}".encode("utf-8")
    return hashlib.sha256(salted).hexdigest()[:16]


def schema_inspect(dataset_path: str | Path) -> SchemaReport:
    """
    Load a CSV dataset and produce a full schema report.

    Hashes configured PII columns immediately so raw identifiers never
    persist past this function call. Flags columns with >80% nulls as
    unreliable for hypothesis generation.

    Args:
        dataset_path: Path to the source CSV file.

    Returns:
        SchemaReport describing the dataset structure and quality.
    """
    df = pd.read_csv(dataset_path)
    pii_hashed: list[str] = []

    for pii_col in config.PII_COLUMNS:
        if pii_col in df.columns:
            df[pii_col] = df[pii_col].apply(hash_pii)
            pii_hashed.append(pii_col)

    columns_meta: list[dict[str, Any]] = []
    unreliable: list[str] = []

    for col in df.columns:
        null_pct = float(df[col].isna().mean())
        is_numeric = pd.api.types.is_numeric_dtype(df[col])
        cardinality = int(df[col].nunique(dropna=True))

        meta = {
            "name": col,
            "dtype": "numeric" if is_numeric else "categorical",
            "null_pct": null_pct,
            "cardinality": cardinality,
            "min": float(df[col].min()) if is_numeric and df[col].notna().any() else None,
            "max": float(df[col].max()) if is_numeric and df[col].notna().any() else None,
            "mean": float(df[col].mean()) if is_numeric and df[col].notna().any() else None,
            "std": float(df[col].std()) if is_numeric and df[col].notna().any() else None,
            "is_unreliable": null_pct > 0.80,
            "sample_values": df[col].dropna().head(3).tolist(),
        }
        columns_meta.append(meta)
        if meta["is_unreliable"]:
            unreliable.append(col)

    return SchemaReport(
        row_count=len(df),
        columns=columns_meta,
        unreliable_columns=unreliable,
        pii_columns_hashed=pii_hashed,
    )


def build_sqlite_db(dataset_path: str | Path, table_name: str = "claims") -> str:
    """
    Load a CSV into an in-memory-backed SQLite file for safe querying.

    Args:
        dataset_path: Path to the source CSV file.
        table_name: Name of the table to create.

    Returns:
        Path to the SQLite database file.
    """
    df = pd.read_csv(dataset_path)
    db_path = str(config.OUTPUTS_DIR / "agent_runtime.db")
    conn = sqlite3.connect(db_path)
    df.to_sql(table_name, conn, if_exists="replace", index=False)
    conn.close()
    return db_path


def execute_query(
    sql: str,
    db_path: str,
    timeout_sec: int = config.QUERY_TIMEOUT_SEC,
) -> QueryResult:
    """
    Execute a read-only SQL query against the sandboxed SQLite database.

    Enforces: keyword blocklist, row cap injection, execution timeout.

    Args:
        sql: SQL SELECT statement.
        db_path: Path to the SQLite database file.
        timeout_sec: Maximum execution time in seconds.

    Returns:
        QueryResult with rows, timing, and error information.
    """
    upper_sql = sql.upper()
    for keyword in config.PROHIBITED_SQL_KEYWORDS:
        if keyword in upper_sql:
            return QueryResult(
                rows=[],
                row_count=0,
                execution_time_ms=0.0,
                columns_returned=[],
                error=None,
                safety_violation=f"prohibited_keyword:{keyword}",
            )

    if "LIMIT" not in upper_sql:
        sql = f"SELECT * FROM ({sql.rstrip(';')}) AS capped_subquery LIMIT {config.MAX_ROWS_PER_QUERY}"

    start = time.perf_counter()
    try:
        conn = sqlite3.connect(db_path, timeout=timeout_sec)
        cursor = conn.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        elapsed_ms = (time.perf_counter() - start) * 1000
        return QueryResult(
            rows=rows,
            row_count=len(rows),
            execution_time_ms=elapsed_ms,
            columns_returned=columns,
            error=None,
        )
    except sqlite3.Error as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return QueryResult(
            rows=[],
            row_count=0,
            execution_time_ms=elapsed_ms,
            columns_returned=[],
            error=str(exc),
        )


def generate_chart(
    hypothesis_id: str,
    rows: list[dict],
    test_type: str,
    group_column: str | None,
    value_column: str | None,
    statistical_result: dict[str, Any] | None,
    figures_dir: Path,
) -> str | None:
    """
    Render a PNG chart summarizing one successful hypothesis's result.

    Chart type is selected by test_type: grouped bar with SEM error bars for
    t-test/anova, a grouped count bar for chi-square, a scatter plot with a
    fitted line for regression, and a histogram for descriptive. A plotting
    failure must never abort the agent run, so every exception is caught and
    logged rather than raised.

    Args:
        hypothesis_id: Hypothesis identifier, used for the filename and title.
        rows: The raw query result rows the statistical test was computed on.
        test_type: One of "t-test", "anova", "chi-square", "regression", "descriptive".
        group_column: Categorical column used for group comparison tests.
        value_column: Numeric column being tested.
        statistical_result: The dict returned by validator._run_statistical_test.
        figures_dir: Directory to write the PNG into (must already exist).

    Returns:
        The chart's filename (relative to figures_dir), or None if no chart
        could be built for this test_type/column combination or rendering failed.
    """
    try:
        df = pd.DataFrame(rows)
        fig, ax = plt.subplots(figsize=(6, 4))
        p_value = (statistical_result or {}).get("p_value")
        title_suffix = f" (p={p_value:.4g})" if isinstance(p_value, (int, float)) else ""

        if test_type in ("t-test", "anova") and group_column and value_column:
            numeric = pd.to_numeric(df[value_column], errors="coerce")
            grouped = df.assign(_value=numeric).groupby(group_column)["_value"]
            means, sems = grouped.mean(), grouped.sem()
            ax.bar(means.index.astype(str), means.values, yerr=sems.values, capsize=4)
            ax.set_xlabel(group_column)
            ax.set_ylabel(value_column)
        elif test_type == "chi-square" and group_column and value_column:
            contingency = pd.crosstab(df[group_column], df[value_column])
            contingency.plot(kind="bar", ax=ax)
            ax.set_xlabel(group_column)
            ax.set_ylabel("count")
            ax.legend(title=value_column, fontsize="small")
        elif test_type == "regression" and value_column:
            predictor = (statistical_result or {}).get("direction") or group_column
            if not predictor or predictor not in df.columns:
                plt.close(fig)
                return None
            x = pd.to_numeric(df[predictor], errors="coerce")
            y = pd.to_numeric(df[value_column], errors="coerce")
            mask = x.notna() & y.notna()
            ax.scatter(x[mask], y[mask], alpha=0.3, s=10)
            if mask.sum() > 1:
                slope, intercept = np.polyfit(x[mask], y[mask], 1)
                x_line = np.linspace(x[mask].min(), x[mask].max(), 100)
                ax.plot(x_line, slope * x_line + intercept, color="red", linewidth=1.5)
            ax.set_xlabel(predictor)
            ax.set_ylabel(value_column)
        elif value_column and value_column in df.columns:
            numeric = pd.to_numeric(df[value_column], errors="coerce").dropna()
            ax.hist(numeric, bins=30)
            ax.set_xlabel(value_column)
            ax.set_ylabel("count")
        else:
            plt.close(fig)
            return None

        ax.set_title(f"{hypothesis_id}{title_suffix}")
        fig.tight_layout()
        filename = f"{hypothesis_id}_chart.png"
        fig.savefig(figures_dir / filename, dpi=110)
        plt.close(fig)
        return filename
    except Exception:
        logger.warning("Chart generation failed for %s", hypothesis_id, exc_info=True)
        return None


def save_report(
    run_id: str,
    successful_analyses: list[dict],
    aborted_hypotheses: list[str],
    error_log: list[dict],
) -> dict[str, str]:
    """
    Write the Markdown report and JSON metadata for a completed run.

    Args:
        run_id: Unique identifier for this run.
        successful_analyses: List of completed analysis dicts.
        aborted_hypotheses: Hypothesis IDs that exhausted retries.
        error_log: Full error chain across the run.

    Returns:
        Dict with paths to the generated report, metadata, and figures dir.
    """
    run_dir = config.OUTPUTS_DIR / f"analysis_{run_id}"
    figures_dir = run_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    report_path = run_dir / "analysis_report.md"
    metadata_path = run_dir / "run_metadata.json"

    md_lines = [
        f"# Analysis Report — Run {run_id}",
        "",
        f"**Successful analyses:** {len(successful_analyses)}",
        f"**Aborted hypotheses:** {len(aborted_hypotheses)}",
        "",
    ]
    for analysis in successful_analyses:
        md_lines.append(f"## {analysis.get('hypothesis_id', 'Unknown')}: {analysis.get('statement', '')}")
        md_lines.append(f"- **Query:** `{analysis.get('final_sql', '')}`")
        md_lines.append(f"- **Rows returned:** {analysis.get('row_count', 0)}")
        md_lines.append(f"- **Retries:** {analysis.get('retry_count', 0)}")
        stat = analysis.get("statistical_result") or {}
        if stat:
            result_line = (
                f"- **Statistical result:** {stat.get('test_name')}, "
                f"statistic={stat.get('statistic')}, p={stat.get('p_value')}, "
                f"significant={stat.get('significant')}"
            )
            if "p_value_adjusted" in stat:
                result_line += (
                    f" (FDR-adjusted p={stat['p_value_adjusted']:.4f}, "
                    f"significant_adjusted={stat.get('significant_adjusted')})"
                )
            md_lines.append(result_line)
        warnings = analysis.get("warnings") or []
        if warnings:
            md_lines.append(f"- **Caveats:** {'; '.join(warnings)}")
        if analysis.get("figure_path"):
            md_lines.append(f"![{analysis.get('hypothesis_id', '')} chart](figures/{analysis['figure_path']})")
        md_lines.append("")

    if aborted_hypotheses:
        md_lines.append("## Aborted Hypotheses")
        md_lines.extend(f"- {h}" for h in aborted_hypotheses)
        md_lines.append("")

    report_path.write_text("\n".join(md_lines), encoding="utf-8")

    metadata = {
        "run_id": run_id,
        "model_version": config.MODEL_NAME,
        "successful_count": len(successful_analyses),
        "aborted_hypotheses": aborted_hypotheses,
        "error_log": error_log,
        "analyses": successful_analyses,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")

    return {
        "report_path": str(report_path),
        "metadata_path": str(metadata_path),
        "figures_dir": str(figures_dir),
        "success": True,
    }
