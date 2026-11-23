"""Response models and derived-metric rules."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

MICROS_PER_USD = 1_000_000


class Platform(str, Enum):
    """An enum so FastAPI rejects unknown values with a 422 before any handler
    code runs."""

    meta = "meta"
    google = "google"
    linkedin = "linkedin"


class GroupBy(str, Enum):
    campaign = "campaign"
    platform = "platform"


class HealthStatus(str, Enum):
    passed = "pass"
    warn = "warn"
    fail = "fail"


class Metrics(BaseModel):
    spend_usd: float
    impressions: int
    clicks: int
    # Ratio, not a percentage. Formatting is the client's job; returning 0.0219
    # rather than 2.19 means a consumer never has to guess the convention.
    ctr: float | None = Field(
        description="clicks / impressions. null when impressions is 0."
    )
    cpc: float | None = Field(
        description="spend_usd / clicks. null when clicks is 0."
    )


def derive_metrics(spend_micros: int, impressions: int, clicks: int) -> Metrics:
    """Stored micros to reportable figures.

    CPC is null, not zero, when clicks is zero: spend against no clicks has an
    undefined cost per click, and 0.0 would read as free traffic and sort to the
    top of a cheapest-CPC view. Infinity is not valid JSON.

    This is the only place division happens — every sum upstream is exact
    integer arithmetic.
    """
    spend_usd = spend_micros / MICROS_PER_USD
    return Metrics(
        spend_usd=round(spend_usd, 2),
        impressions=impressions,
        clicks=clicks,
        ctr=round(clicks / impressions, 6) if impressions else None,
        cpc=round(spend_usd / clicks, 4) if clicks else None,
    )


class MetricGroup(Metrics):
    key: str
    platform: str
    campaign: str | None = None


class AppliedFilters(BaseModel):
    """Echoed back so a consumer can prove which slice produced the totals."""

    platform: list[str] | None = None
    date_from: date | None = None
    date_to: date | None = None
    group_by: GroupBy
    include_quarantined: bool


class MetricsResponse(BaseModel):
    filters: AppliedFilters
    totals: Metrics
    groups: list[MetricGroup]
    # How many deliveries fed these numbers, and how many were withheld. The UI
    # shows this so a total is never presented without its provenance.
    contributing_deliveries: int
    excluded_quarantined_deliveries: int


class DailyMetric(Metrics):
    """Row-level output; every figure carries the delivery and source line it
    came from."""

    platform: str
    campaign: str
    campaign_raw: str
    date: date
    delivery_id: str
    source_path: str
    source_line: int
    quarantined: bool


class CheckResultOut(BaseModel):
    check_id: str
    level: str
    severity: str
    status: str
    message: str
    details: dict[str, Any]


class DeliverySummary(BaseModel):
    delivery_id: str
    platform: str
    week_start: date
    source_path: str
    is_present: bool
    status: HealthStatus
    quarantined: bool
    rows_parsed: int
    rows_accepted: int
    rows_rejected: int
    size_bytes: int | None
    content_sha256: str | None
    ingested_at: datetime
    checks_failed: int
    critical_failures: int


class DeliveryDetail(DeliverySummary):
    """Full quality report for one delivery."""

    checks: list[CheckResultOut]
    spend_usd: float
    impressions: int
    clicks: int


class CadenceCell(BaseModel):
    platform: str
    week_start: date
    status: HealthStatus | None = None
    delivery_id: str | None = None
    is_present: bool
    quarantined: bool = False


class CadenceResponse(BaseModel):
    """Platform x week grid, so a missing delivery renders as a hole."""

    weeks: list[date]
    platforms: list[str]
    cells: list[CadenceCell]


class IngestionSummaryOut(BaseModel):
    run_id: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float
    deliveries_processed: int
    passed: int
    warned: int
    failed: int
    quarantined: int
    rows_accepted: int
    rows_rejected: int
    rows_deduplicated: int
    unrecognised_files: list[str]


class IngestionRunOut(BaseModel):
    run_id: str
    started_at: datetime
    finished_at: datetime | None
    deliveries_processed: int
    passed: int
    warned: int
    failed: int
    rows_accepted: int
    rows_rejected: int
