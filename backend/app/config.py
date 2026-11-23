"""Paths, the expected delivery grid, and health thresholds."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

# Derived from __file__ rather than cwd: the pipeline runs from the repo root,
# from backend/, and from pytest's temp directory.
APP_DIR = Path(__file__).resolve().parent
BACKEND_DIR = APP_DIR.parent
REPO_ROOT = BACKEND_DIR.parent

DATA_DIR = REPO_ROOT / "data"
DELIVERIES_DIR = DATA_DIR / "deliveries"
EXCHANGE_RATES_PATH = DATA_DIR / "exchange_rates.json"

DB_PATH = BACKEND_DIR / "campaign_hub.db"

# Overridable so tests get a throwaway database and so moving to Postgres is a
# config change: CAMPAIGN_HUB_DATABASE_URL=postgresql+psycopg://user:pw@host/db
DATABASE_URL = os.environ.get("CAMPAIGN_HUB_DATABASE_URL") or f"sqlite:///{DB_PATH}"


PLATFORMS: tuple[str, ...] = ("meta", "google", "linkedin")

EXPECTED_WEEK_STARTS: tuple[date, ...] = (
    date(2026, 6, 1),
    date(2026, 6, 8),
    date(2026, 6, 15),
    date(2026, 6, 22),
    date(2026, 6, 29),
)

# The reporting period closes at month end; the short final week falls out of
# this bound rather than being special-cased.
PERIOD_END: date = date(2026, 6, 30)


def expected_dates(week_start: date) -> list[date]:
    """Dates a delivery should cover, clipped to PERIOD_END."""
    days = [week_start + timedelta(days=offset) for offset in range(7)]
    return [day for day in days if day <= PERIOD_END]


# Drives the row-count and coverage checks. Derived from the delivered data,
# which is circular — a campaign disappearing from an export would go unnoticed.
# Should come from a campaign registry.
EXPECTED_CAMPAIGN_COUNT: dict[str, int] = {
    "meta": 5,
    "google": 5,
    "linkedin": 3,
}


# Treated as a unit error rather than a budget change. Week-on-week variance
# sits under 2x in practice, so 10x clears real movement while catching the
# cents-for-dollars case comfortably.
SPEND_MAGNITUDE_RATIO = 10.0

# Below this many comparable deliveries the baseline is not meaningful and the
# magnitude check reports SKIP instead of guessing.
SPEND_MAGNITUDE_MIN_BASELINE_FILES = 2

# Rejected-row share (0-100) that downgrades a delivery to FAIL even when no
# CRITICAL check fired.
REJECTED_ROW_PCT_FAIL = 10.0
