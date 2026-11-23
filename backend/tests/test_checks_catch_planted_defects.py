"""Defect coverage, pinned.

Each known defect in the sample deliveries is tied to the check that must catch
it, so a refactor that stops detecting one fails by name rather than silently.
"""

from __future__ import annotations

import pytest

from app.quality import classify, run_checks
from app.quality.registry import CRITICAL, FAIL

# (source file, check that must FAIL, what is wrong with it)
PLANTED_DEFECTS = [
    (
        "meta_ads_2026-06-08.csv",
        "file.spend_magnitude",
        "spend reported in cents under a spend_usd header (~100x)",
    ),
    (
        "google_ads_2026-06-15_resend.csv",
        "file.content_duplicate",
        "byte-identical re-send of the 06-15 delivery",
    ),
    (
        "linkedin_ads_2026-06-22.json",
        "delivery.present",
        "an entire weekly delivery never arrived",
    ),
    (
        "google_ads_2026-06-01.csv",
        "file.internal_duplicates",
        "8 campaign/day rows repeated inside one file",
    ),
    (
        "meta_ads_2026-06-01.csv",
        "row.date_parseable",
        "06/31/2026 is not a real calendar date",
    ),
    (
        "meta_ads_2026-06-01.csv",
        "file.date_format_consistency",
        "one row uses ISO while the rest use MM/DD/YYYY",
    ),
    (
        "meta_ads_2026-06-01.csv",
        "file.campaign_naming",
        "lowercase variants of three campaign names",
    ),
    (
        "meta_ads_2026-06-15.csv",
        "file.campaign_naming",
        "trailing-whitespace variants of two campaign names",
    ),
    (
        "meta_ads_2026-06-15.csv",
        "row.schema_complete",
        "three rows with an empty clicks cell",
    ),
    (
        "meta_ads_2026-06-22.csv",
        "row.numeric_valid",
        "negative spend of -111.41",
    ),
    (
        "linkedin_ads_2026-06-08.json",
        "row.schema_complete",
        "five records omit the clicks key entirely",
    ),
]


def _outcomes(run_context, source_path):
    ctx = next(c for c in run_context.deliveries if c.source_path == source_path)
    return ctx, {chk.check_id: outcome for chk, outcome in run_checks(ctx)}


@pytest.mark.parametrize(
    ("source_path", "check_id", "description"),
    PLANTED_DEFECTS,
    ids=[f"{path}::{check}" for path, check, _ in PLANTED_DEFECTS],
)
def test_planted_defect_is_detected(run_context, source_path, check_id, description):
    _ctx, outcomes = _outcomes(run_context, source_path)
    assert check_id in outcomes, f"{check_id} did not run for {source_path}"
    assert outcomes[check_id].status == FAIL, (
        f"{check_id} did not flag {source_path} ({description}); "
        f"got {outcomes[check_id].status}: {outcomes[check_id].message}"
    )


@pytest.mark.parametrize(
    "source_path",
    [
        "google_ads_2026-06-08.csv",
        "google_ads_2026-06-22.csv",
        "google_ads_2026-06-29.csv",
        "linkedin_ads_2026-06-01.json",
        "linkedin_ads_2026-06-15.json",
        "meta_ads_2026-06-29.csv",
    ],
)
def test_clean_deliveries_are_not_flagged(run_context, source_path):
    """No false positives. A suite that flags everything catches every defect
    and is useless."""
    ctx, _ = _outcomes(run_context, source_path)
    graded = classify(ctx, run_checks(ctx))
    assert graded.status == "pass", f"{source_path} unexpectedly {graded.status}: {graded.reasons}"
    assert not graded.quarantined


QUARANTINED = {
    "meta_ads_2026-06-08.csv",
    "google_ads_2026-06-15_resend.csv",
    "linkedin_ads_2026-06-22.json",
}


def test_exactly_the_untrustworthy_deliveries_are_quarantined(run_context):
    """Precise in both directions: too wide throws away good weeks, too narrow
    lets a unit error reach the dashboard."""
    quarantined = {
        ctx.source_path
        for ctx in run_context.deliveries
        if classify(ctx, run_checks(ctx)).quarantined
    }
    assert quarantined == QUARANTINED


def test_row_level_defects_never_quarantine_a_delivery(run_context):
    """A bad row must not condemn the good rows around it.

    This delivery grades `fail` on rejected-row volume while its valid rows
    still count — incomplete, not untrustworthy. That distinction is why status
    and quarantine are separate fields.
    """
    ctx = next(
        c
        for c in run_context.deliveries
        if c.source_path == "linkedin_ads_2026-06-08.json"
    )
    graded = classify(ctx, run_checks(ctx))
    assert graded.status == "fail"
    assert graded.quarantined is False
    assert len(ctx.rows) == 16
    assert len(ctx.rejections) == 5
    # Only rejected-row volume drove the grade; no CRITICAL check fired.
    assert not [
        chk for chk, out in graded.results if out.status == FAIL and chk.severity == CRITICAL
    ]
