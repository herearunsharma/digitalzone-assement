"""Idempotency: ingest three times, compare stored state exactly."""

from __future__ import annotations

import json

from sqlalchemy import select

from app.db import SessionLocal
from app.models import CheckResult, Delivery, MetricFact
from app.pipeline.ingest import ingest


def _snapshot() -> dict[str, list]:
    """Everything that must be identical between runs.

    `Delivery.ingested_at` is excluded — it records when we last looked at the
    file and is expected to move. Every column describing the data is included.
    """
    session = SessionLocal()
    try:
        facts = [
            (
                f.delivery_id,
                f.platform,
                f.campaign_key,
                f.campaign_name,
                f.campaign_raw,
                f.date.isoformat(),
                f.spend_usd_micros,
                f.impressions,
                f.clicks,
                f.source_line,
            )
            for f in session.scalars(
                select(MetricFact).order_by(
                    MetricFact.delivery_id, MetricFact.campaign_key, MetricFact.date
                )
            )
        ]
        checks = [
            (
                c.delivery_id,
                c.check_id,
                c.level,
                c.severity,
                c.status,
                c.message,
                json.dumps(c.details, sort_keys=True, default=str),
            )
            for c in session.scalars(
                select(CheckResult).order_by(
                    CheckResult.delivery_id, CheckResult.check_id
                )
            )
        ]
        deliveries = [
            (
                d.delivery_id,
                d.platform,
                d.week_start.isoformat(),
                d.source_path,
                d.content_sha256,
                d.is_present,
                d.status,
                d.quarantined,
                d.rows_parsed,
                d.rows_accepted,
                d.rows_rejected,
            )
            for d in session.scalars(select(Delivery).order_by(Delivery.delivery_id))
        ]
        return {"facts": facts, "checks": checks, "deliveries": deliveries}
    finally:
        session.close()


def test_reingesting_is_byte_identical(ingested):
    first = _snapshot()

    ingest()
    second = _snapshot()

    ingest()
    third = _snapshot()

    assert first == second == third


def test_reingesting_does_not_duplicate_rows(ingested):
    before = _snapshot()
    assert len(before["facts"]) == 394  # 402 parsed - 8 within-file duplicates
    assert len(before["deliveries"]) == 16  # 15 files + 1 slot that never arrived

    ingest()
    after = _snapshot()

    assert len(after["facts"]) == len(before["facts"])
    assert len(after["deliveries"]) == len(before["deliveries"])
    assert len(after["checks"]) == len(before["checks"])


def test_delivery_ids_are_derived_from_path_not_content():
    """A corrected re-delivery under the same filename must replace the previous
    data; a content-derived id would land it as a second delivery."""
    from app.pipeline.discover import delivery_id_for

    assert delivery_id_for("meta_ads_2026-06-01.csv") == delivery_id_for(
        "meta_ads_2026-06-01.csv"
    )
    assert delivery_id_for("meta_ads_2026-06-01.csv") != delivery_id_for(
        "meta_ads_2026-06-08.csv"
    )


def test_duplicate_resolution_is_order_stable(run_context):
    """Byte-identical files resolve by processing order, so discovery sorts by
    path. Without it a re-run could flip which file is quarantined and the
    comparison above would fail intermittently."""
    google = [
        ctx.source_path
        for ctx in run_context.deliveries
        if ctx.platform == "google" and ctx.is_present
    ]
    assert google == sorted(google)
    assert google.index("google_ads_2026-06-15.csv") < google.index(
        "google_ads_2026-06-15_resend.csv"
    )
