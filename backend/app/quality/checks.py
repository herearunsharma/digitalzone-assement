"""Quality checks across row, file and delivery level.

Importing this module populates the registry.

Severity drives quarantine: CRITICAL means the delivery's numbers cannot be
trusted as a whole, WARNING means usable but incomplete. No row-level check is
CRITICAL — the adapter already dropped the bad row, so the delivery is
incomplete rather than untrustworthy, and binning a good week over one empty
cell loses more than it protects.
"""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import median
from typing import Any

from app.adapters import Reason, RowRejection
from app.config import (
    SPEND_MAGNITUDE_MIN_BASELINE_FILES,
    SPEND_MAGNITUDE_RATIO,
)
from app.quality.context import DeliveryContext
from app.quality.dedup import partition_duplicates
from app.quality.registry import (
    CRITICAL,
    DELIVERY,
    FILE,
    ROW,
    WARNING,
    CheckOutcome,
    check,
    fail,
    ok,
    skip,
)

# Bounded so a catastrophic file cannot write a multi-megabyte blob into the
# check_result.details column.
SAMPLE_LIMIT = 10


def _rejection_samples(items: list[RowRejection]) -> list[dict[str, Any]]:
    return [
        {
            "line": item.line,
            "reason_code": item.reason_code,
            "field": item.field,
            "value": item.value,
            "message": item.message,
        }
        for item in items[:SAMPLE_LIMIT]
    ]


def _by_reason(ctx: DeliveryContext, *codes: str) -> list[RowRejection]:
    wanted = set(codes)
    return [r for r in ctx.rejections if r.reason_code in wanted]


# --- Row level --------------------------------------------------------------


@check(
    id="row.schema_complete",
    level=ROW,
    severity=WARNING,
    description="Every record carries all required fields, non-empty.",
)
def row_schema_complete(ctx: DeliveryContext) -> CheckOutcome:
    offenders = _by_reason(ctx, Reason.MISSING_FIELD)
    if not offenders:
        return ok(f"all {ctx.records_seen} records carry the required fields")
    fields = sorted({item.field for item in offenders})
    return fail(
        f"{len(offenders)} of {ctx.records_seen} records are missing a required "
        f"field ({', '.join(fields)})",
        affected_records=len(offenders),
        records_seen=ctx.records_seen,
        fields=fields,
        samples=_rejection_samples(offenders),
    )


@check(
    id="row.date_parseable",
    level=ROW,
    severity=WARNING,
    description="Every record's date is a real calendar date in an accepted format.",
)
def row_date_parseable(ctx: DeliveryContext) -> CheckOutcome:
    offenders = _by_reason(ctx, Reason.DATE_INVALID, Reason.DATE_UNPARSEABLE)
    if not offenders:
        return ok(f"all {ctx.records_seen} record dates parsed")
    return fail(
        f"{len(offenders)} record(s) carry an unusable date",
        affected_records=len(offenders),
        records_seen=ctx.records_seen,
        samples=_rejection_samples(offenders),
    )


@check(
    id="row.date_in_window",
    level=ROW,
    severity=WARNING,
    description="Every accepted row falls inside the week the filename declares.",
)
def row_date_in_window(ctx: DeliveryContext) -> CheckOutcome:
    """Guards the month-first reading of Meta dates: a day-first export would
    push rows outside the declared week instead of failing silently."""
    window = set(ctx.expected_dates)
    if not window:
        return skip("no expected date window for this week")
    strays = [row for row in ctx.rows if row.date not in window]
    if not strays:
        return ok(
            f"all {len(ctx.rows)} rows fall within "
            f"{min(window).isoformat()}..{max(window).isoformat()}"
        )
    return fail(
        f"{len(strays)} row(s) fall outside the declared week",
        window_start=min(window).isoformat(),
        window_end=max(window).isoformat(),
        affected_rows=len(strays),
        samples=[
            {"line": row.source_line, "campaign": row.campaign_raw, "date": row.date.isoformat()}
            for row in strays[:SAMPLE_LIMIT]
        ],
    )


@check(
    id="row.numeric_valid",
    level=ROW,
    severity=WARNING,
    description="Spend, impressions and clicks are numeric and non-negative.",
)
def row_numeric_valid(ctx: DeliveryContext) -> CheckOutcome:
    offenders = _by_reason(ctx, Reason.NUMERIC_INVALID, Reason.NUMERIC_NEGATIVE)
    if not offenders:
        return ok(f"all {ctx.records_seen} records carry valid non-negative metrics")
    negatives = [item for item in offenders if item.reason_code == Reason.NUMERIC_NEGATIVE]
    return fail(
        f"{len(offenders)} record(s) carry an invalid metric "
        f"({len(negatives)} negative)",
        affected_records=len(offenders),
        negative_records=len(negatives),
        records_seen=ctx.records_seen,
        samples=_rejection_samples(offenders),
    )


@check(
    id="row.currency_known",
    level=ROW,
    severity=WARNING,
    description="Every record's currency has a rate supplied by finance.",
)
def row_currency_known(ctx: DeliveryContext) -> CheckOutcome:
    offenders = _by_reason(ctx, Reason.CURRENCY_UNKNOWN)
    if not offenders:
        return ok("every record's currency has a supplied exchange rate")
    currencies = sorted({item.value for item in offenders})
    return fail(
        f"{len(offenders)} record(s) report a currency with no exchange rate "
        f"({', '.join(currencies)})",
        affected_records=len(offenders),
        currencies=currencies,
        samples=_rejection_samples(offenders),
    )


@check(
    id="row.metric_coherence",
    level=ROW,
    severity=WARNING,
    description="Metrics are internally consistent (clicks <= impressions, etc).",
)
def row_metric_coherence(ctx: DeliveryContext) -> CheckOutcome:
    """Rows that parse cleanly but cannot describe reality — more clicks than
    impressions, or spend billed against nothing served."""
    problems: list[dict[str, Any]] = []
    for row in ctx.rows:
        if row.clicks > row.impressions:
            problems.append(
                {
                    "line": row.source_line,
                    "campaign": row.campaign_raw,
                    "date": row.date.isoformat(),
                    "issue": "clicks_exceed_impressions",
                    "clicks": row.clicks,
                    "impressions": row.impressions,
                }
            )
        elif row.impressions == 0 and row.spend_usd_micros > 0:
            problems.append(
                {
                    "line": row.source_line,
                    "campaign": row.campaign_raw,
                    "date": row.date.isoformat(),
                    "issue": "spend_without_impressions",
                    "spend_usd": row.spend_usd_micros / 1_000_000,
                }
            )
    if not problems:
        return ok(f"all {len(ctx.rows)} rows are internally consistent")
    return fail(
        f"{len(problems)} row(s) carry incoherent metrics",
        affected_rows=len(problems),
        samples=problems[:SAMPLE_LIMIT],
    )


# --- File level -------------------------------------------------------------


@check(
    id="file.parseable",
    level=FILE,
    severity=CRITICAL,
    description="The file could be read as its declared format.",
    runs_on_broken_delivery=True,
)
def file_parseable(ctx: DeliveryContext) -> CheckOutcome:
    if not ctx.is_present:
        return skip("no file to parse — the delivery never arrived")
    if ctx.parse_error:
        return fail(f"file could not be parsed: {ctx.parse_error}", error=ctx.parse_error)
    return ok(f"parsed {ctx.records_seen} records")


@check(
    id="file.header_schema",
    level=FILE,
    severity=CRITICAL,
    description="The file presents every column/key the adapter requires.",
)
def file_header_schema(ctx: DeliveryContext) -> CheckOutcome:
    """CRITICAL because nothing can be normalized without the column, even if
    the file itself parsed."""
    if ctx.header is None:
        return skip("no header available for this format")
    present = set(ctx.header)
    missing = [column for column in ctx.expected_columns if column not in present]
    unexpected = sorted(present - set(ctx.expected_columns))
    if missing:
        return fail(
            f"missing required column(s): {', '.join(missing)}",
            missing=missing,
            expected=list(ctx.expected_columns),
            found=sorted(present),
        )
    return ok(
        "all required columns present",
        expected=list(ctx.expected_columns),
        # Not a failure (platforms add fields), but recorded so schema drift is
        # visible before it becomes a bug.
        unexpected=unexpected,
    )


@check(
    id="file.content_duplicate",
    level=FILE,
    severity=CRITICAL,
    description="The file's content has not already been delivered under another name.",
)
def file_content_duplicate(ctx: DeliveryContext) -> CheckOutcome:
    """A re-send under a new filename. Quarantining because every row is
    individually valid — which is what makes it dangerous: ingesting both
    copies doubles the week with nothing looking wrong at row level.
    """
    if not ctx.content_sha256 or ctx.run is None:
        return skip("no content hash available")
    first = ctx.run.first_with_hash(ctx.content_sha256)
    if first is None or first.delivery_id == ctx.delivery_id:
        return ok("content hash is unique among this run's deliveries")
    return fail(
        f"byte-identical to {first.source_path!r} — counting both would double "
        f"this week's figures",
        duplicate_of_delivery_id=first.delivery_id,
        duplicate_of_path=first.source_path,
        sha256=ctx.content_sha256,
    )


@check(
    id="file.internal_duplicates",
    level=FILE,
    severity=WARNING,
    description="No campaign/day observation is repeated inside the file.",
)
def file_internal_duplicates(ctx: DeliveryContext) -> CheckOutcome:
    """Not CRITICAL: the pipeline drops these through the same
    partition_duplicates() call, so stored totals are already correct. Flagged
    anyway — a platform repeating rows means its export is unhealthy."""
    _kept, dropped = partition_duplicates(ctx.rows)
    if not dropped:
        return ok(f"all {len(ctx.rows)} rows are unique on (campaign, date)")
    return fail(
        f"{len(dropped)} duplicate row(s) repeated an existing (campaign, date) "
        f"and were dropped before storage",
        duplicate_rows=len(dropped),
        rows_seen=len(ctx.rows),
        rows_kept=len(_kept),
        samples=[
            {
                "line": row.source_line,
                "campaign": row.campaign_raw,
                "date": row.date.isoformat(),
                "spend_usd": row.spend_usd_micros / 1_000_000,
            }
            for row in dropped[:SAMPLE_LIMIT]
        ],
    )


@check(
    id="file.row_count",
    level=FILE,
    severity=WARNING,
    description="The file holds one observation per campaign per day of its week.",
)
def file_row_count(ctx: DeliveryContext) -> CheckOutcome:
    """Deduplicated rows against campaigns x days. Counting raw rows would
    double-report the duplication defect and hide a genuine shortfall under an
    inflated total."""
    expected = ctx.expected_row_count
    if expected == 0:
        return skip("no expected row count configured for this platform")
    kept, _dropped = partition_duplicates(ctx.rows)
    actual = len(kept)
    if actual == expected:
        return ok(f"{actual} observations, exactly as expected")
    shortfall = expected - actual
    return fail(
        f"{actual} usable observations, expected {expected} "
        f"({abs(shortfall)} {'missing' if shortfall > 0 else 'extra'})",
        expected=expected,
        actual=actual,
        difference=actual - expected,
        expected_campaigns=ctx.expected_campaign_count,
        expected_days=len(ctx.expected_dates),
        rejected_records=len(ctx.rejections),
    )


@check(
    id="file.campaign_naming",
    level=FILE,
    severity=WARNING,
    description="Each campaign is spelled consistently within the file.",
)
def file_campaign_naming(ctx: DeliveryContext) -> CheckOutcome:
    """Case and whitespace variants of one campaign name.

    canonical_campaign_key already merges these, so totals are correct. Flagged
    because drifting naming is one upstream change away from a variant the
    canonicalisation does not merge.
    """
    variants: dict[str, set[str]] = defaultdict(set)
    for row in ctx.rows:
        variants[row.campaign_key].add(row.campaign_raw)

    drifting = {key: sorted(names) for key, names in variants.items() if len(names) > 1}
    if not drifting:
        return ok(f"{len(variants)} campaign(s), each spelled consistently")
    return fail(
        f"{len(drifting)} campaign(s) appear under multiple spellings "
        f"(merged on a canonical key, so totals are unaffected)",
        affected_campaigns=len(drifting),
        # repr() so trailing whitespace survives whatever renders the report.
        variants={key: [repr(name) for name in names] for key, names in drifting.items()},
    )


@check(
    id="file.date_format_consistency",
    level=FILE,
    severity=WARNING,
    description="The file states its dates in one consistent representation.",
)
def file_date_format_consistency(ctx: DeliveryContext) -> CheckOutcome:
    """A file mixing date representations parses cleanly, since the adapter
    falls back across formats. Keep the tolerance — refusing the row would lose
    real spend — but report it, because the next drift may be a format we parse
    wrongly rather than not at all.
    """
    formats: dict[str, list] = defaultdict(list)
    for row in ctx.rows:
        if row.date_format:
            formats[row.date_format].append(row)
    if len(formats) <= 1:
        only = next(iter(formats), "unknown")
        return ok(f"all {len(ctx.rows)} rows state dates as {only}", formats=[only])

    # Minority formats are the anomalous rows worth naming.
    ranked = sorted(formats.items(), key=lambda item: len(item[1]), reverse=True)
    dominant, dominant_rows = ranked[0]
    minority = ranked[1:]
    return fail(
        f"file mixes {len(formats)} date formats: {len(dominant_rows)} rows as "
        f"{dominant}, " + ", ".join(f"{len(rows)} as {fmt}" for fmt, rows in minority),
        dominant_format=dominant,
        format_counts={fmt: len(rows) for fmt, rows in ranked},
        samples=[
            {
                "line": row.source_line,
                "campaign": row.campaign_raw,
                "date": row.date.isoformat(),
                "format": fmt,
            }
            for fmt, rows in minority
            for row in rows[:SAMPLE_LIMIT]
        ],
    )


@check(
    id="file.spend_magnitude",
    level=FILE,
    severity=CRITICAL,
    description="The file's spend scale matches the platform's established baseline.",
)
def file_spend_magnitude(ctx: DeliveryContext) -> CheckOutcome:
    """Unit errors — spend reported in cents under a dollars header.

    Median rather than mean on both sides: a handful of outlier rows cannot move
    a median, so only a whole-file scale shift trips the check. A mean would
    fire on one large campaign day, and would itself be dragged upward by the
    very file we are trying to detect.

    Quarantining, because every row parses perfectly and nothing else in the
    system would notice the week is 100x out.
    """
    if not ctx.rows:
        return skip("no rows to measure")
    if ctx.run is None:
        return skip("no run context available")

    comparable = [
        sibling
        for sibling in ctx.run.siblings(ctx)
        if sibling.rows and not sibling.is_broken
    ]
    if len(comparable) < SPEND_MAGNITUDE_MIN_BASELINE_FILES:
        return skip(
            f"only {len(comparable)} comparable deliveries for {ctx.platform}; "
            f"need {SPEND_MAGNITUDE_MIN_BASELINE_FILES} to establish a baseline",
            comparable_deliveries=len(comparable),
        )

    mine = median(row.spend_usd_micros for row in ctx.rows)
    baseline = median(
        row.spend_usd_micros for sibling in comparable for row in sibling.rows
    )
    if baseline <= 0 or mine <= 0:
        return skip("baseline or file median is zero — ratio undefined")

    ratio = max(mine / baseline, baseline / mine)
    details: dict[str, Any] = {
        "file_median_spend_usd": round(mine / 1_000_000, 4),
        "platform_baseline_median_spend_usd": round(baseline / 1_000_000, 4),
        "ratio": round(ratio, 2),
        "threshold": SPEND_MAGNITUDE_RATIO,
        "baseline_deliveries": len(comparable),
    }
    if ratio < SPEND_MAGNITUDE_RATIO:
        return ok(
            f"median row spend is {ratio:.2f}x the {ctx.platform} baseline, "
            f"within the {SPEND_MAGNITUDE_RATIO}x tolerance",
            **details,
        )

    # Nearest power of ten lets the report name a likely cause: ~100x reads as
    # cents-under-dollars.
    inferred = 10 ** round(math.log10(ratio))
    details["inferred_factor"] = inferred
    direction = "higher" if mine > baseline else "lower"
    hint = (
        "consistent with values reported in cents under a dollars header"
        if inferred == 100 and mine > baseline
        else "likely a unit or scaling error in the export"
    )
    return fail(
        f"median row spend is {ratio:.0f}x {direction} than the {ctx.platform} "
        f"baseline (~{inferred}x) — {hint}",
        **details,
    )


# --- Delivery level ---------------------------------------------------------


@check(
    id="delivery.present",
    level=DELIVERY,
    severity=CRITICAL,
    description="A file arrived for this platform/week slot.",
    runs_on_broken_delivery=True,
)
def delivery_present(ctx: DeliveryContext) -> CheckOutcome:
    """The only check that fires on a delivery that does not exist.

    Nothing row- or file-level can see a missing delivery; it is only visible
    against the expected platform x week grid, which is why the pipeline
    materialises a row for absent slots rather than skipping them.
    """
    if ctx.is_present:
        return ok(f"delivery received: {ctx.source_path}")
    return fail(
        f"no {ctx.platform} delivery arrived for the week beginning "
        f"{ctx.week_start.isoformat()}",
        platform=ctx.platform,
        week_start=ctx.week_start.isoformat(),
        expected_filename=ctx.source_path,
        expected_observations=ctx.expected_row_count,
    )


@check(
    id="delivery.coverage",
    level=DELIVERY,
    severity=WARNING,
    description="Every campaign reports every day of the delivery's week.",
)
def delivery_coverage(ctx: DeliveryContext) -> CheckOutcome:
    """Which campaign/day observations are absent; file.row_count covers how
    many. Gaps here are usually downstream of a row rejection — an unparseable
    date removing a campaign-day.
    """
    expected_days = ctx.expected_dates
    if not expected_days or not ctx.rows:
        return skip("no rows or no expected window to compare against")

    seen: dict[str, set[str]] = defaultdict(set)
    display: dict[str, str] = {}
    for row in ctx.rows:
        seen[row.campaign_key].add(row.date.isoformat())
        display.setdefault(row.campaign_key, row.campaign_raw.strip())

    wanted = {day.isoformat() for day in expected_days}
    gaps = {
        display[key]: sorted(wanted - days)
        for key, days in seen.items()
        if wanted - days
    }
    if not gaps:
        return ok(
            f"all {len(seen)} campaign(s) report all {len(wanted)} day(s) of the week"
        )
    missing_total = sum(len(days) for days in gaps.values())
    return fail(
        f"{missing_total} campaign-day observation(s) missing across "
        f"{len(gaps)} campaign(s)",
        missing_observations=missing_total,
        affected_campaigns=len(gaps),
        gaps=gaps,
    )
