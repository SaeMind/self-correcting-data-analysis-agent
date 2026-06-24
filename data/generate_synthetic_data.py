"""
generate_synthetic_data.py — Produces a 10,000-record synthetic healthcare
claims dataset for the Self-Correcting Data Analysis Agent.

Intentionally injects realistic data quality issues (nulls, a small negative-
cost rate, missing diagnosis codes) so the validator and self-correction
logic have real failure modes to handle on first run.

Author: Andrew Lee
"""

import numpy as np
import pandas as pd

np.random.seed(42)

N_RECORDS = 10_000
N_MEMBERS = 2_500
N_PROVIDERS = 300

DIAGNOSIS_CODES = [
    "E11.9",   # Type 2 diabetes
    "I10",     # Essential hypertension
    "J45.909", # Asthma
    "M54.5",   # Low back pain
    "F32.9",   # Major depressive disorder
    "N18.3",   # Chronic kidney disease stage 3
    "I25.10",  # Coronary artery disease
    "E78.5",   # Hyperlipidemia
]

SERVICE_TYPES = ["Inpatient", "Outpatient", "Emergency", "Primary Care", "Specialist"]


def generate_dataset() -> pd.DataFrame:
    """Build the synthetic claims DataFrame with realistic quality issues."""
    member_id = np.random.randint(100_000, 100_000 + N_MEMBERS, size=N_RECORDS)
    provider_id = np.random.randint(900_000, 900_000 + N_PROVIDERS, size=N_RECORDS)

    age = np.clip(np.random.normal(loc=52, scale=18, size=N_RECORDS), 0, 95).astype(int)

    base_dates = pd.date_range("2024-01-01", "2025-12-31", periods=N_RECORDS)
    service_date = np.random.choice(base_dates, size=N_RECORDS)

    diagnosis_code = np.random.choice(DIAGNOSIS_CODES, size=N_RECORDS)
    service_type = np.random.choice(SERVICE_TYPES, size=N_RECORDS, p=[0.10, 0.35, 0.10, 0.30, 0.15])

    # Cost correlates with age and service type to give hypotheses real signal.
    base_cost = np.random.gamma(shape=2.5, scale=400, size=N_RECORDS)
    age_multiplier = 1 + (age / 100)
    service_multiplier = np.select(
        [service_type == "Inpatient", service_type == "Emergency"],
        [3.5, 2.0],
        default=1.0,
    )
    amount = np.round(base_cost * age_multiplier * service_multiplier, 2)

    df = pd.DataFrame({
        "member_id": member_id,
        "provider_id": provider_id,
        "age": age,
        "service_date": service_date,
        "diagnosis_code": diagnosis_code,
        "service_type": service_type,
        "amount": amount,
        "chronic_condition_flag": np.where(
            np.isin(diagnosis_code, ["E11.9", "I10", "N18.3", "I25.10"]), 1, 0
        ),
        "readmission_30day": np.random.binomial(1, p=0.12, size=N_RECORDS),
    })

    # Inject realistic data quality issues for the agent to detect and correct.
    null_idx = np.random.choice(df.index, size=int(0.05 * N_RECORDS), replace=False)
    df.loc[null_idx, "diagnosis_code"] = np.nan

    negative_cost_idx = np.random.choice(df.index, size=int(0.02 * N_RECORDS), replace=False)
    df.loc[negative_cost_idx, "amount"] = -df.loc[negative_cost_idx, "amount"]

    missing_age_idx = np.random.choice(df.index, size=int(0.01 * N_RECORDS), replace=False)
    df.loc[missing_age_idx, "age"] = np.nan

    return df


if __name__ == "__main__":
    from pathlib import Path

    df = generate_dataset()
    output_path = Path(__file__).resolve().parent / "claims_sample.csv"
    df.to_csv(output_path, index=False)
    print(f"Generated {len(df)} records -> {output_path}")
    print(f"Negative cost records: {(df['amount'] < 0).sum()}")
    print(f"Null diagnosis_code records: {df['diagnosis_code'].isna().sum()}")
    print(f"Null age records: {df['age'].isna().sum()}")
