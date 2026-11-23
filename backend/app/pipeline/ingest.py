"""Ingestion pipeline.

Three phases: parse every discovered file, complete the expected platform/week
grid with contexts for slots that produced no file, then check and persist one
delivery per transaction.

Parsing must finish before anything is written because the cross-delivery checks
— duplicate content, spend baselines, cadence — need every sibling in hand. A
streaming pass would make a verdict depend on position in the directory listing.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.adapters import (
    CanonicalRow,
    FileParseError,
    PlatformAdapter,
    RawRecord,
    build_adapters,
)
from app.config import DELIVERIES_DIR, EXPECTED_WEEK_STARTS, PLATFORMS
from app.currency import load_rates
from app.db import init_db, session_scope
from app.models import CheckResult, Delivery, IngestionRun, MetricFact
from app.pipeline.discover import (
    DiscoveredFile,
    UnrecognisedFile,
    delivery_id_for,
    discover,
    expected_filename,
)
from app.quality import (
    HEALTH_FAIL,
    HEALTH_PASS,
    HEALTH_WARN,
    DeliveryContext,
    GradedDelivery,
    RunContext,
    classify,
    partition_duplicates,
    run_checks,
)


@dataclass
class IngestionSummary:

    run_id: str
    started_at: datetime
    finished_at: datetime
    deliveries_processed: int = 0
    passed: int = 0
    warned: int = 0
    failed: int = 0
    quarantined: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    rows_deduplicated: int = 0
    unrecognised_files: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


def _header_of(records: list[RawRecord]) -> tuple[str, ...] | None:
    """Union across records, not the first record's keys — a JSON delivery may
    legitimately omit an optional key on some records."""
    keys: set[str] = set()
    for record in records:
        keys.update(key for key in record.data if key is not None)
    return tuple(sorted(keys)) if keys else None


def _context_for_file(found: DiscoveredFile) -> DeliveryContext:
    """Parse and normalize one delivery. Never raises."""
    rows: list[CanonicalRow] = []
    rejections = []
    header: tuple[str, ...] | None = None
    parse_error: str | None = None

    try:
        records = found.adapter.parse(found.path)
        header = _header_of(records)
        for record in records:
            outcome = found.adapter.normalize(record)
            if isinstance(outcome, CanonicalRow):
                rows.append(outcome)
            else:
                rejections.append(outcome)
    except FileParseError as exc:
        parse_error = str(exc)
    except Exception as exc:  # noqa: BLE001
        # Broad on purpose: "malformed" includes shapes the adapter's own error
        # handling did not anticipate. Becomes a failed file.parseable check and
        # the run continues with the next file.
        parse_error = f"unexpected {type(exc).__name__}: {exc}"

    return DeliveryContext(
        delivery_id=found.delivery_id,
        platform=found.platform,
        week_start=found.week_start,
        source_path=found.relative,
        path=found.path,
        content_sha256=found.sha256,
        size_bytes=found.size_bytes,
        is_present=True,
        header=header,
        expected_columns=found.adapter.expected_columns,
        rows=rows,
        rejections=rejections,
        parse_error=parse_error,
    )


def _context_for_missing(adapter: PlatformAdapter, week_start) -> DeliveryContext:
    """A delivery slot that produced no file.

    Materialised rather than skipped: a missing week is only visible against the
    expected grid, and can only be displayed if it has a row.
    """
    relative = expected_filename(adapter, week_start)
    return DeliveryContext(
        delivery_id=delivery_id_for(relative),
        platform=adapter.platform,
        week_start=week_start,
        source_path=relative,
        path=None,
        content_sha256=None,
        size_bytes=None,
        is_present=False,
        header=None,
        expected_columns=adapter.expected_columns,
        rows=[],
        rejections=[],
    )


def build_run_context(
    deliveries_dir: Path, adapters: tuple[PlatformAdapter, ...]
) -> tuple[RunContext, list[UnrecognisedFile]]:
    """Parse everything, then fill in the slots that produced no file."""
    discovered, unrecognised = discover(deliveries_dir, adapters)
    run = RunContext()

    # Discovery order is sorted, which is what keeps first_with_hash stable.
    for found in discovered:
        run.add(_context_for_file(found))

    by_platform = {adapter.platform: adapter for adapter in adapters}
    seen = {(ctx.platform, ctx.week_start) for ctx in run.deliveries}
    for platform in PLATFORMS:
        adapter = by_platform.get(platform)
        if adapter is None:
            continue
        for week_start in EXPECTED_WEEK_STARTS:
            if (platform, week_start) not in seen:
                run.add(_context_for_missing(adapter, week_start))

    return run, unrecognised


def _display_names(rows: list[CanonicalRow]) -> dict[str, str]:
    """One display name per canonical campaign key: most frequent spelling wins.

    The alphabetical tie-break is not cosmetic — without it two equally common
    spellings could swap between runs and break idempotency.
    """
    tally: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        tally[row.campaign_key][row.campaign_raw.strip()] += 1
    return {
        key: sorted(counter.items(), key=lambda item: (-item[1], item[0]))[0][0]
        for key, counter in tally.items()
    }


def _persist(
    session: Session,
    ctx: DeliveryContext,
    graded: GradedDelivery,
    kept: list[CanonicalRow],
    ingested_at: datetime,
) -> None:
    """Write one delivery, replacing anything previously stored for it.

    Scoped delete-then-insert inside the caller's transaction. An upsert alone
    would leave stale rows behind when a re-delivery contains fewer rows than
    before; deleting the slice first makes stored state a true mirror of the
    file.
    """
    session.query(MetricFact).filter(
        MetricFact.delivery_id == ctx.delivery_id
    ).delete(synchronize_session=False)
    session.query(CheckResult).filter(
        CheckResult.delivery_id == ctx.delivery_id
    ).delete(synchronize_session=False)

    delivery = session.get(Delivery, ctx.delivery_id)
    if delivery is None:
        delivery = Delivery(delivery_id=ctx.delivery_id)
        session.add(delivery)

    delivery.platform = ctx.platform
    delivery.week_start = ctx.week_start
    delivery.source_path = ctx.source_path
    delivery.content_sha256 = ctx.content_sha256
    delivery.size_bytes = ctx.size_bytes
    delivery.is_present = ctx.is_present
    delivery.status = graded.status
    delivery.quarantined = graded.quarantined
    delivery.rows_parsed = ctx.records_seen
    delivery.rows_accepted = len(kept)
    delivery.rows_rejected = len(ctx.rejections)
    delivery.ingested_at = ingested_at

    names = _display_names(kept)
    for row in kept:
        session.add(
            MetricFact(
                delivery_id=ctx.delivery_id,
                platform=row.platform,
                campaign_key=row.campaign_key,
                campaign_name=names.get(row.campaign_key, row.campaign_raw.strip()),
                campaign_raw=row.campaign_raw,
                date=row.date,
                spend_usd_micros=row.spend_usd_micros,
                impressions=row.impressions,
                clicks=row.clicks,
                source_line=row.source_line,
            )
        )

    for chk, outcome in graded.results:
        session.add(
            CheckResult(
                delivery_id=ctx.delivery_id,
                check_id=chk.check_id,
                level=chk.level,
                severity=chk.severity,
                status=outcome.status,
                message=outcome.message[:512],
                details=outcome.details,
            )
        )


def ingest(deliveries_dir: Path | None = None) -> IngestionSummary:
    """Run the full pipeline. Idempotent, so safe to call repeatedly."""
    started_at = datetime.now(UTC).replace(tzinfo=None)
    summary = IngestionSummary(
        run_id=str(uuid.uuid4()), started_at=started_at, finished_at=started_at
    )

    init_db()
    adapters = build_adapters(load_rates())
    run, unrecognised = build_run_context(deliveries_dir or DELIVERIES_DIR, adapters)
    summary.unrecognised_files = [item.relative for item in unrecognised]

    for ctx in run.deliveries:
        results = run_checks(ctx)
        graded = classify(ctx, results)

        # Same function file.internal_duplicates reports on, so the report and
        # the stored data cannot disagree.
        kept, dropped = partition_duplicates(ctx.rows)

        # Per delivery rather than per run: a failure on one must not roll back
        # the deliveries already written cleanly.
        with session_scope() as session:
            _persist(session, ctx, graded, kept, started_at)

        summary.deliveries_processed += 1
        summary.rows_accepted += len(kept)
        summary.rows_rejected += len(ctx.rejections)
        summary.rows_deduplicated += len(dropped)
        summary.quarantined += 1 if graded.quarantined else 0
        if graded.status == HEALTH_PASS:
            summary.passed += 1
        elif graded.status == HEALTH_WARN:
            summary.warned += 1
        elif graded.status == HEALTH_FAIL:
            summary.failed += 1

    summary.finished_at = datetime.now(UTC).replace(tzinfo=None)

    with session_scope() as session:
        session.add(
            IngestionRun(
                run_id=summary.run_id,
                started_at=summary.started_at,
                finished_at=summary.finished_at,
                deliveries_processed=summary.deliveries_processed,
                passed=summary.passed,
                warned=summary.warned,
                failed=summary.failed,
                rows_accepted=summary.rows_accepted,
                rows_rejected=summary.rows_rejected,
            )
        )

    return summary


def main() -> None:
    summary = ingest()
    print(f"run {summary.run_id}  ({summary.duration_seconds:.2f}s)")
    print(
        f"  deliveries : {summary.deliveries_processed} "
        f"(pass {summary.passed} / warn {summary.warned} / fail {summary.failed})"
    )
    print(f"  quarantined: {summary.quarantined}")
    print(
        f"  rows       : {summary.rows_accepted} accepted, "
        f"{summary.rows_rejected} rejected, "
        f"{summary.rows_deduplicated} duplicates dropped"
    )
    if summary.unrecognised_files:
        print(f"  unrecognised: {', '.join(summary.unrecognised_files)}")


if __name__ == "__main__":
    main()
