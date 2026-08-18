"""
agent.py — Main agentic loop for the Self-Correcting Data Analysis Agent.

Orchestrates: schema inspection -> hypothesis generation -> query planning ->
execution -> validation -> self-correction -> report generation.

Requires ANTHROPIC_API_KEY in the environment (see .env.example) to run the
hypothesis generation, query planning, and error correction stages. The
deterministic tool layer (schema_inspect, execute_query, validate_results,
save_report) runs with no external dependency and is covered by
tests/test_agent.py.

Usage:
    python src/agent.py --dataset data/claims_sample.csv

Author: Andrew Lee
"""

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

import anthropic

from src import config, tools, validator

logging.basicConfig(
    level=logging.INFO,
    format=config.LOG_FORMAT,
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def generate_run_id() -> str:
    """Return a short unique identifier for this agent run."""
    return uuid.uuid4().hex[:8]


def _extract_text(response: anthropic.types.Message) -> str:
    """
    Return the first text block's content from a Messages API response.

    Reasoning-capable models (claude-sonnet-5 and later) may prepend a
    `thinking` content block before the actual `text` block even when the
    request doesn't explicitly disable thinking, so `content[0]` cannot be
    assumed to be text — indexing it directly raises AttributeError on a
    ThinkingBlock. Skip past any non-text blocks instead.
    """
    for block in response.content:
        if block.type == "text":
            return block.text
    raise ValueError(f"No text block found in response content: {response.content!r}")


def _strip_code_fences(text: str) -> str:
    """
    Remove markdown code fences from a model response, if present.

    Models sometimes wrap JSON/SQL output in ```json ... ``` or ``` ... ```
    blocks despite explicit instructions not to. This strips them so
    downstream parsing doesn't fail on the literal backtick characters.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # drop opening fence (and optional language tag)
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        elif lines and lines[-1].strip().endswith("```"):
            lines[-1] = lines[-1].rstrip("`").rstrip()
        text = "\n".join(lines).strip()
    return text


def call_hypothesis_generator(
    client: anthropic.Anthropic, schema: tools.SchemaReport
) -> list[dict]:
    """
    Ask Claude to propose ranked analytical hypotheses from the schema.

    Args:
        client: Initialized Anthropic client.
        schema: SchemaReport from schema_inspect().

    Returns:
        List of hypothesis dicts parsed from the model's JSON response.
    """
    system_prompt = (
        "You are a clinical data scientist. Given a dataset schema, propose "
        f"{config.MAX_HYPOTHESES_PER_RUN} ranked analytical hypotheses. Each "
        "hypothesis must be answerable using only the available columns and "
        "must have clinical interpretability. Respond ONLY with a JSON array, "
        "no preamble, no markdown fences. Each object must have fields: "
        "hypothesis_id, statement, columns_required, test_type, priority, "
        "clinical_rationale, group_column, value_column. "
        "test_type selection rules: use 't-test' only when comparing exactly "
        "2 groups; use 'anova' when comparing 3 or more groups on a "
        "continuous outcome; use 'chi-square' for association between two "
        "categorical variables; use 'regression' for a continuous predictor "
        "and continuous outcome; use 'descriptive' only when the hypothesis "
        "makes no comparison or significance claim. If a hypothesis claims "
        "groups 'differ significantly', it must use t-test, anova, or "
        "chi-square — never 'descriptive'. For 'regression', set "
        "group_column to the exact name of the single continuous predictor "
        "column (not a grouping variable) and value_column to the "
        "continuous outcome column."
    )
    response = client.messages.create(
        model=config.MODEL_NAME,
        max_tokens=config.MAX_TOKENS_HYPOTHESIS,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=system_prompt,
        messages=[{"role": "user", "content": schema.to_prompt_string()}],
    )
    text = _strip_code_fences(_extract_text(response))
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse hypothesis JSON. Raw response:\n%s", text)
        raise ValueError(
            f"Claude returned non-JSON output for hypothesis generation: {exc}"
        ) from exc


def call_query_planner(
    client: anthropic.Anthropic,
    hypothesis: dict,
    schema: tools.SchemaReport,
    error_context: str | None = None,
) -> str:
    """
    Ask Claude to write a safe, parameterized SQL query for a hypothesis.

    Args:
        client: Initialized Anthropic client.
        hypothesis: Hypothesis dict to test.
        schema: SchemaReport for column reference.
        error_context: Prior failure context, if this is a retry.

    Returns:
        Raw SQL query string.
    """
    system_prompt = (
        "You are writing a single safe SQL SELECT query for SQLite against a "
        "table named 'claims'. Never use DELETE, DROP, INSERT, UPDATE, "
        "TRUNCATE, or ALTER. The query must return raw, row-level data: do "
        "NOT use GROUP BY, aggregate functions (AVG, SUM, COUNT, MIN, MAX), "
        "or window functions (any OVER clause, including aggregate-as-window "
        "patterns like AVG(x) OVER (...)). All statistics are computed by a "
        "separate validation layer from the raw rows you return — SQL-side "
        "aggregation will fail validation. Include every column listed in "
        "'Columns required' by its exact name with no aliasing. Return ONLY "
        "the SQL query text, no explanation, no markdown fences."
    )
    user_content = (
        f"Hypothesis: {hypothesis['statement']}\n"
        f"Test type: {hypothesis['test_type']}\n"
        f"Columns required: {hypothesis['columns_required']}\n\n"
        f"{schema.to_prompt_string()}"
    )
    if error_context:
        user_content += f"\n\nPrevious attempt failed: {error_context}\nWrite a corrected query."

    response = client.messages.create(
        model=config.MODEL_NAME,
        max_tokens=config.MAX_TOKENS_QUERY,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    return _strip_code_fences(_extract_text(response)).removeprefix("sql\n")


def call_error_corrector(
    client: anthropic.Anthropic,
    hypothesis: dict,
    failed_sql: str,
    failure_reason: str,
    schema: tools.SchemaReport,
) -> str:
    """
    Ask Claude to rewrite a failed query given a specific failure reason.

    Args:
        client: Initialized Anthropic client.
        hypothesis: Hypothesis dict being tested.
        failed_sql: The query that failed.
        failure_reason: Machine-readable failure code (e.g. "negative_cost").
        schema: SchemaReport for column reference.

    Returns:
        Corrected SQL query string.
    """
    system_prompt = (
        "You are debugging a SQL query that failed validation. Rewrite it to "
        "fix the specific failure reason given. The query must return raw, "
        "row-level data: do NOT use GROUP BY, aggregate functions (AVG, SUM, "
        "COUNT, MIN, MAX), or window functions (any OVER clause, including "
        "aggregate-as-window patterns like AVG(x) OVER (...) — this exact "
        "pattern is a common mistake, do not repeat it). Include every "
        "required column by its exact name with no aliasing. Return ONLY "
        "the corrected SQL, no explanation, no markdown fences."
    )
    user_content = (
        f"Hypothesis: {hypothesis['statement']}\n"
        f"Failed query: {failed_sql}\n"
        f"Failure reason: {failure_reason}\n\n"
        f"{schema.to_prompt_string()}"
    )
    response = client.messages.create(
        model=config.MODEL_NAME,
        max_tokens=config.MAX_TOKENS_CORRECTION,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    return _strip_code_fences(_extract_text(response)).removeprefix("sql\n")


def run_agent(dataset_path: str) -> str:
    """
    Execute the full agentic loop against a dataset.

    Args:
        dataset_path: Path to the input CSV dataset.

    Returns:
        Path to the output directory containing the report and metadata.
    """
    if not config.ANTHROPIC_API_KEY:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY not set. Copy .env.example to .env and add your key."
        )

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    run_id = generate_run_id()
    start_time = datetime.now(timezone.utc)
    logger.info("Run %s started at %s", run_id, start_time.isoformat())

    schema = tools.schema_inspect(dataset_path)
    db_path = tools.build_sqlite_db(dataset_path)
    logger.info("Schema inspected: %d rows, %d columns", schema.row_count, len(schema.columns))

    # Created up front (not inside save_report) so generate_chart() can write
    # into it during the loop below, before the final report is assembled.
    figures_dir = config.OUTPUTS_DIR / f"analysis_{run_id}" / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    hypotheses = call_hypothesis_generator(client, schema)
    hypotheses = sorted(hypotheses, key=lambda h: h.get("priority", 99))[: config.MAX_HYPOTHESES_PER_RUN]
    logger.info("Generated %d hypotheses", len(hypotheses))

    successful_analyses: list[dict] = []
    aborted_hypotheses: list[str] = []
    error_log: list[dict] = []

    for hypothesis in hypotheses:
        h_id = hypothesis["hypothesis_id"]
        retry_count = 0
        sql = None
        error_context = None

        while retry_count <= config.MAX_RETRIES_PER_HYPOTHESIS:
            if retry_count == 0:
                sql = call_query_planner(client, hypothesis, schema)
            else:
                sql = call_error_corrector(client, hypothesis, sql, error_context, schema)

            result = tools.execute_query(sql, db_path)

            if result.safety_violation:
                error_context = f"safety_violation:{result.safety_violation}"
                retry_count += 1
                error_log.append({"hypothesis_id": h_id, "attempt": retry_count, "reason": error_context})
                continue

            if result.error:
                error_context = f"execution_error:{result.error}"
                retry_count += 1
                error_log.append({"hypothesis_id": h_id, "attempt": retry_count, "reason": error_context})
                continue

            report = validator.validate_results(
                rows=result.rows,
                columns_returned=result.columns_returned,
                test_type=hypothesis["test_type"],
                group_column=hypothesis.get("group_column"),
                value_column=hypothesis.get("value_column"),
            )

            if report.passed:
                figure_path = tools.generate_chart(
                    hypothesis_id=h_id,
                    rows=result.rows,
                    test_type=hypothesis["test_type"],
                    group_column=hypothesis.get("group_column"),
                    value_column=hypothesis.get("value_column"),
                    statistical_result=report.statistical_result,
                    figures_dir=figures_dir,
                )
                successful_analyses.append({
                    "hypothesis_id": h_id,
                    "statement": hypothesis["statement"],
                    "final_sql": sql,
                    "row_count": result.row_count,
                    "statistical_result": report.statistical_result,
                    "warnings": report.warnings,
                    "retry_count": retry_count,
                    "figure_path": figure_path,
                })
                logger.info("Hypothesis %s passed after %d retries", h_id, retry_count)
                break

            error_context = f"validation_failure:{report.failure_reason}"
            retry_count += 1
            error_log.append({"hypothesis_id": h_id, "attempt": retry_count, "reason": error_context})

        else:
            pass

        if retry_count > config.MAX_RETRIES_PER_HYPOTHESIS and not any(
            a["hypothesis_id"] == h_id for a in successful_analyses
        ):
            aborted_hypotheses.append(h_id)
            logger.warning("Hypothesis %s aborted after max retries", h_id)

    if len(successful_analyses) < config.MIN_SUCCESSFUL_ANALYSES:
        raise RuntimeError(
            f"Only {len(successful_analyses)} analyses completed; "
            f"minimum required is {config.MIN_SUCCESSFUL_ANALYSES}."
        )

    successful_analyses = validator.apply_multiple_comparison_correction(successful_analyses)

    output_paths = tools.save_report(run_id, successful_analyses, aborted_hypotheses, error_log)
    logger.info("Run %s complete. Report: %s", run_id, output_paths["report_path"])
    return output_paths["report_path"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Self-Correcting Data Analysis Agent")
    parser.add_argument(
        "--dataset",
        type=str,
        default=str(config.DEFAULT_DATASET_PATH),
        help="Path to the input CSV dataset.",
    )
    args = parser.parse_args()
    report_path = run_agent(args.dataset)
    print(f"Report generated: {report_path}")


if __name__ == "__main__":
    main()
