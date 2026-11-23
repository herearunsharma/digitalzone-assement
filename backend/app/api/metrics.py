"""Campaign metrics endpoints."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.api.errors import ProblemDetail
from app.api.schemas import (
    AppliedFilters,
    DailyMetric,
    GroupBy,
    MetricGroup,
    MetricsResponse,
    Platform,
    derive_metrics,
)
from app.db import get_session
from app.models import Delivery, MetricFact

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])


def _validate_range(date_from: date | None, date_to: date | None) -> None:
    """422 rather than an empty 200: a typo must not masquerade as "no data for
    that period"."""
    if date_from and date_to and date_from > date_to:
        raise ProblemDetail(
            # Literal rather than the starlette constant, which was renamed
            # (UNPROCESSABLE_ENTITY -> UNPROCESSABLE_CONTENT) and would tie us
            # to a version range.
            status_code=422,
            title="Invalid date range",
            detail=f"date_from ({date_from}) is after date_to ({date_to}).",
            errors=[{"field": "date_from", "message": "must be on or before date_to"}],
        )


def _apply_filters(
    stmt: Select,
    platforms: list[Platform] | None,
    date_from: date | None,
    date_to: date | None,
    include_quarantined: bool,
) -> Select:
    """Join facts to their delivery and narrow to the requested slice.

    Quarantine lives on the delivery, so every metric query must join. Keeping
    that in one helper is what stops a new endpoint reporting untrusted rows.
    """
    stmt = stmt.join(Delivery, MetricFact.delivery_id == Delivery.delivery_id)
    if not include_quarantined:
        stmt = stmt.where(Delivery.quarantined.is_(False))
    if platforms:
        stmt = stmt.where(MetricFact.platform.in_([p.value for p in platforms]))
    if date_from:
        stmt = stmt.where(MetricFact.date >= date_from)
    if date_to:
        stmt = stmt.where(MetricFact.date <= date_to)
    return stmt


@router.get("", response_model=MetricsResponse, summary="Aggregated campaign metrics")
def list_metrics(
    platform: list[Platform] | None = Query(
        default=None, description="Repeatable. Omit for all platforms."
    ),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None, description="Inclusive."),
    group_by: GroupBy = Query(default=GroupBy.campaign),
    include_quarantined: bool = Query(
        default=False,
        description=(
            "Include deliveries whose numbers failed a critical quality check. "
            "Off by default: these figures are known to be untrustworthy."
        ),
    ),
    session: Session = Depends(get_session),
) -> MetricsResponse:
    _validate_range(date_from, date_to)

    spend = func.sum(MetricFact.spend_usd_micros)
    impressions = func.sum(MetricFact.impressions)
    clicks = func.sum(MetricFact.clicks)

    if group_by is GroupBy.campaign:
        keys = [MetricFact.platform, MetricFact.campaign_key, MetricFact.campaign_name]
    else:
        keys = [MetricFact.platform]

    grouped = _apply_filters(
        select(*keys, spend, impressions, clicks),
        platform,
        date_from,
        date_to,
        include_quarantined,
    ).group_by(*keys)

    groups: list[MetricGroup] = []
    for row in session.execute(grouped):
        if group_by is GroupBy.campaign:
            plat, campaign_key, campaign_name, spend_sum, impr_sum, click_sum = row
            key = f"{plat}:{campaign_key}"
        else:
            plat, spend_sum, impr_sum, click_sum = row
            campaign_name, key = None, plat
        metrics = derive_metrics(int(spend_sum or 0), int(impr_sum or 0), int(click_sum or 0))
        groups.append(
            MetricGroup(
                key=key, platform=plat, campaign=campaign_name, **metrics.model_dump()
            )
        )

    groups.sort(key=lambda g: g.spend_usd, reverse=True)

    # Own aggregate rather than a sum over `groups`: summing rounded figures
    # would drift from the true total.
    totals_row = session.execute(
        _apply_filters(
            select(spend, impressions, clicks),
            platform,
            date_from,
            date_to,
            include_quarantined,
        )
    ).one()
    totals = derive_metrics(
        int(totals_row[0] or 0), int(totals_row[1] or 0), int(totals_row[2] or 0)
    )

    contributing = session.execute(
        _apply_filters(
            select(func.count(func.distinct(MetricFact.delivery_id))),
            platform,
            date_from,
            date_to,
            include_quarantined,
        )
    ).scalar_one()

    excluded = session.execute(
        select(func.count()).select_from(Delivery).where(Delivery.quarantined.is_(True))
    ).scalar_one()

    return MetricsResponse(
        filters=AppliedFilters(
            platform=[p.value for p in platform] if platform else None,
            date_from=date_from,
            date_to=date_to,
            group_by=group_by,
            include_quarantined=include_quarantined,
        ),
        totals=totals,
        groups=groups,
        contributing_deliveries=int(contributing or 0),
        excluded_quarantined_deliveries=0 if include_quarantined else int(excluded or 0),
    )


@router.get(
    "/daily",
    response_model=list[DailyMetric],
    summary="Row-level metrics with full provenance",
)
def list_daily_metrics(
    platform: list[Platform] | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    campaign: str | None = Query(
        default=None, description="Canonical campaign key, e.g. 'summer sale'."
    ),
    include_quarantined: bool = Query(default=False),
    limit: int = Query(default=500, ge=1, le=5000),
    session: Session = Depends(get_session),
) -> list[DailyMetric]:
    """Each row names its delivery and source line, so any figure in the UI is
    traceable without reading pipeline code."""
    _validate_range(date_from, date_to)

    stmt = _apply_filters(
        select(MetricFact, Delivery.source_path, Delivery.quarantined),
        platform,
        date_from,
        date_to,
        include_quarantined,
    )
    if campaign:
        stmt = stmt.where(MetricFact.campaign_key == campaign.strip().casefold())
    stmt = stmt.order_by(
        MetricFact.date, MetricFact.platform, MetricFact.campaign_key
    ).limit(limit)

    out: list[DailyMetric] = []
    for fact, source_path, quarantined in session.execute(stmt):
        metrics = derive_metrics(fact.spend_usd_micros, fact.impressions, fact.clicks)
        out.append(
            DailyMetric(
                platform=fact.platform,
                campaign=fact.campaign_name,
                campaign_raw=fact.campaign_raw,
                date=fact.date,
                delivery_id=fact.delivery_id,
                source_path=source_path,
                source_line=fact.source_line,
                quarantined=quarantined,
                **metrics.model_dump(),
            )
        )
    return out
