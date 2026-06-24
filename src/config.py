"""
config.py — Centralized configuration for the Self-Correcting Data Analysis Agent.

All paths, constants, and environment variables are defined here.
No other module should hardcode a path or magic number that originates here.

Author: Andrew Lee
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# DIRECTORY PATHS
# ──────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
SRC_DIR = PROJECT_ROOT / "src"

DEFAULT_DATASET_PATH = DATA_DIR / "claims_sample.csv"

# ──────────────────────────────────────────────
# ANTHROPIC API CONFIGURATION
# ──────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL_NAME = os.getenv("AGENT_MODEL", "claude-sonnet-4-6")
MAX_TOKENS_HYPOTHESIS = 2000
MAX_TOKENS_QUERY = 1000
MAX_TOKENS_CORRECTION = 1000

# ──────────────────────────────────────────────
# AGENT BEHAVIOR CONSTANTS
# ──────────────────────────────────────────────

MAX_RETRIES_PER_HYPOTHESIS = 3
MIN_SUCCESSFUL_ANALYSES = 3
MAX_HYPOTHESES_PER_RUN = 5

# ──────────────────────────────────────────────
# SQL SAFETY CONSTANTS
# ──────────────────────────────────────────────

PROHIBITED_SQL_KEYWORDS = (
    "DELETE", "DROP", "INSERT", "UPDATE", "TRUNCATE", "ALTER",
    "CREATE", "REPLACE", "ATTACH", "DETACH", "PRAGMA",
)
QUERY_TIMEOUT_SEC = 30
MAX_ROWS_PER_QUERY = 1_000_000

# ──────────────────────────────────────────────
# VALIDATION THRESHOLDS
# ──────────────────────────────────────────────

NULL_RATE_FAIL_THRESHOLD = 0.50
DIAGNOSIS_NULL_WARN_THRESHOLD = 0.10
IQR_OUTLIER_WARN_THRESHOLD = 0.30
ZSCORE_FLAG_THRESHOLD = 5.0
MIN_AGE = 0
MAX_AGE = 130
SIGNIFICANCE_ALPHA = 0.05

# ──────────────────────────────────────────────
# PII HANDLING
# ──────────────────────────────────────────────

PII_COLUMNS = ("member_id", "provider_id")
HASH_ALGORITHM = "sha256"
HASH_SALT = os.getenv("PII_HASH_SALT", "self-correcting-agent-default-salt")

# ──────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
LOG_FILE = OUTPUTS_DIR / "agent.log"

# Ensure required directories exist at import time.
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
