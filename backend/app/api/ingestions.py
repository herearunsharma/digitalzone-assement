"""Ingestion trigger and run history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import IngestionRunOut, IngestionSummaryOut
from app.db import get_session
from app.models import IngestionRun
from app.pipeline.ingest import ingest

router = APIRouter(prefix="/api/v1/ingestions", tags=["ingestion"])


@router.post(
    "",
    response_model=IngestionSummaryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Run the ingestion pipeline",
)
def trigger_ingestion() -> IngestionSummaryOut:
    """Ingest every delivery.

    Synchronous 201 rather than 202 with a job id: the current volume completes
    in well under a second, and a queue would add a polling endpoint for no
    benefit. Revisit once runs reach the multi-second range.

    No separate re-ingest verb — ingestion is idempotent, so there is no
    difference between the two operations.
    """
    summary = ingest()
    return IngestionSummaryOut(
        run_id=summary.run_id,
        started_at=summary.started_at,
        finished_at=summary.finished_at,
        duration_seconds=round(summary.duration_seconds, 3),
        deliveries_processed=summary.deliveries_processed,
        passed=summary.passed,
        warned=summary.warned,
        failed=summary.failed,
        quarantined=summary.quarantined,
        rows_accepted=summary.rows_accepted,
        rows_rejected=summary.rows_rejected,
        rows_deduplicated=summary.rows_deduplicated,
        unrecognised_files=summary.unrecognised_files,
    )


@router.get("", response_model=list[IngestionRunOut], summary="Ingestion run history")
def list_runs(
    limit: int = Query(default=20, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[IngestionRunOut]:
    runs = session.scalars(
        select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(limit)
    ).all()
    return [
        IngestionRunOut(
            run_id=r.run_id,
            started_at=r.started_at,
            finished_at=r.finished_at,
            deliveries_processed=r.deliveries_processed,
            passed=r.passed,
            warned=r.warned,
            failed=r.failed,
            rows_accepted=r.rows_accepted,
            rows_rejected=r.rows_rejected,
        )
        for r in runs
    ]
