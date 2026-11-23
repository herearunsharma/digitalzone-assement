"""Data-health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import ProblemDetail
from app.api.schemas import (
    CadenceCell,
    CadenceResponse,
    CheckResultOut,
    DeliveryDetail,
    DeliverySummary,
    HealthStatus,
    Platform,
)
from app.config import EXPECTED_WEEK_STARTS, PLATFORMS
from app.db import get_session
from app.models import CheckResult, Delivery, MetricFact
from app.quality.registry import CRITICAL, FAIL

router = APIRouter(prefix="/api/v1/deliveries", tags=["data health"])


def _failure_counts(session: Session) -> dict[str, tuple[int, int]]:
    """(failed checks, critical failures) per delivery in one query, rather than
    an N+1 against check_result as the list view renders."""
    stmt = (
        select(
            CheckResult.delivery_id,
            func.count(),
            func.sum(
                # SQLite has no boolean aggregate; sum a CASE instead.
                func.iif(CheckResult.severity == CRITICAL, 1, 0)
            ),
        )
        .where(CheckResult.status == FAIL)
        .group_by(CheckResult.delivery_id)
    )
    return {
        delivery_id: (int(total or 0), int(critical or 0))
        for delivery_id, total, critical in session.execute(stmt)
    }


def _to_summary(delivery: Delivery, counts: dict[str, tuple[int, int]]) -> DeliverySummary:
    failed, critical = counts.get(delivery.delivery_id, (0, 0))
    return DeliverySummary(
        delivery_id=delivery.delivery_id,
        platform=delivery.platform,
        week_start=delivery.week_start,
        source_path=delivery.source_path,
        is_present=delivery.is_present,
        status=HealthStatus(delivery.status),
        quarantined=delivery.quarantined,
        rows_parsed=delivery.rows_parsed,
        rows_accepted=delivery.rows_accepted,
        rows_rejected=delivery.rows_rejected,
        size_bytes=delivery.size_bytes,
        content_sha256=delivery.content_sha256,
        ingested_at=delivery.ingested_at,
        checks_failed=failed,
        critical_failures=critical,
    )


@router.get("", response_model=list[DeliverySummary], summary="All deliveries with health")
def list_deliveries(
    platform: list[Platform] | None = Query(default=None),
    status_filter: list[HealthStatus] | None = Query(
        default=None,
        alias="status",
        description="Repeatable: pass, warn, fail.",
    ),
    session: Session = Depends(get_session),
) -> list[DeliverySummary]:
    stmt = select(Delivery)
    if platform:
        stmt = stmt.where(Delivery.platform.in_([p.value for p in platform]))
    if status_filter:
        stmt = stmt.where(Delivery.status.in_([s.value for s in status_filter]))
    # Week then platform, so the list reads as a calendar.
    stmt = stmt.order_by(Delivery.week_start, Delivery.platform, Delivery.source_path)

    counts = _failure_counts(session)
    return [_to_summary(d, counts) for d in session.scalars(stmt)]


# Must stay ahead of /{delivery_id}: FastAPI matches in declaration order, so
# the dynamic route would otherwise swallow "cadence" and 404.
@router.get("/cadence", response_model=CadenceResponse, summary="Platform x week grid")
def delivery_cadence(session: Session = Depends(get_session)) -> CadenceResponse:
    """One cell per expected delivery, including slots that never arrived."""
    by_slot: dict[tuple[str, object], Delivery] = {}
    for delivery in session.scalars(select(Delivery).order_by(Delivery.source_path)):
        key = (delivery.platform, delivery.week_start)
        existing = by_slot.get(key)
        # A slot can hold more than one file (a resend). Show the one that
        # actually counts, so the grid reflects the data behind the numbers.
        if existing is None or (existing.quarantined and not delivery.quarantined):
            by_slot[key] = delivery

    # Every expected slot yields a cell, even with no delivery row, so the UI
    # never has to handle a ragged matrix.
    cells: list[CadenceCell] = []
    for platform in PLATFORMS:
        for week in EXPECTED_WEEK_STARTS:
            found = by_slot.get((platform, week))
            cells.append(
                CadenceCell(
                    platform=platform,
                    week_start=week,
                    status=HealthStatus(found.status) if found else None,
                    delivery_id=found.delivery_id if found else None,
                    is_present=bool(found and found.is_present),
                    quarantined=bool(found and found.quarantined),
                )
            )

    return CadenceResponse(
        weeks=list(EXPECTED_WEEK_STARTS), platforms=list(PLATFORMS), cells=cells
    )


@router.get(
    "/{delivery_id}",
    response_model=DeliveryDetail,
    summary="Full quality report for one delivery",
)
def get_delivery(
    delivery_id: str, session: Session = Depends(get_session)
) -> DeliveryDetail:
    delivery = session.get(Delivery, delivery_id)
    if delivery is None:
        raise ProblemDetail(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Delivery not found",
            detail=f"No delivery with id {delivery_id!r}.",
        )

    checks = session.scalars(
        select(CheckResult)
        .where(CheckResult.delivery_id == delivery_id)
        # Failures first, so the problem leads rather than the passes.
        .order_by(
            func.iif(CheckResult.status == FAIL, 0, 1),
            CheckResult.level,
            CheckResult.check_id,
        )
    ).all()

    totals = session.execute(
        select(
            func.coalesce(func.sum(MetricFact.spend_usd_micros), 0),
            func.coalesce(func.sum(MetricFact.impressions), 0),
            func.coalesce(func.sum(MetricFact.clicks), 0),
        ).where(MetricFact.delivery_id == delivery_id)
    ).one()

    summary = _to_summary(delivery, _failure_counts(session))
    return DeliveryDetail(
        **summary.model_dump(),
        spend_usd=round(int(totals[0]) / 1_000_000, 2),
        impressions=int(totals[1]),
        clicks=int(totals[2]),
        checks=[
            CheckResultOut(
                check_id=c.check_id,
                level=c.level,
                severity=c.severity,
                status=c.status,
                message=c.message,
                details=c.details or {},
            )
            for c in checks
        ],
    )
