"""Runs the registry against a delivery and grades the result."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import REJECTED_ROW_PCT_FAIL
from app.quality.context import DeliveryContext
from app.quality.registry import (
    CRITICAL,
    FAIL,
    WARNING,
    Check,
    CheckOutcome,
    all_checks,
    skip,
)

# Named apart from the per-check PASS/FAIL/SKIP statuses: "this check failed"
# and "this delivery is unhealthy" are different statements.
HEALTH_PASS = "pass"
HEALTH_WARN = "warn"
HEALTH_FAIL = "fail"


@dataclass(frozen=True, slots=True)
class GradedDelivery:
    results: list[tuple[Check, CheckOutcome]]
    status: str
    quarantined: bool
    reasons: list[str]


def run_checks(ctx: DeliveryContext) -> list[tuple[Check, CheckOutcome]]:
    """Execute every registered check against one delivery.

    A check that raises is recorded as SKIP rather than FAIL — it never examined
    the data, so it must not condemn it. The message still carries "check
    raised" so an engine bug stays visible instead of passing as clean.
    """
    results: list[tuple[Check, CheckOutcome]] = []

    for chk in all_checks():
        if ctx.is_broken and not chk.runs_on_broken_delivery:
            reason = (
                "delivery never arrived"
                if not ctx.is_present
                else f"file could not be parsed: {ctx.parse_error}"
            )
            results.append((chk, skip(f"not evaluated — {reason}")))
            continue

        try:
            outcome = chk.fn(ctx)
        except Exception as exc:  # noqa: BLE001 - one bad check must not end the run
            outcome = skip(
                f"check raised {type(exc).__name__} and could not be evaluated: {exc}",
                error_type=type(exc).__name__,
                error=str(exc),
            )
        results.append((chk, outcome))

    return results


def classify(
    ctx: DeliveryContext, results: list[tuple[Check, CheckOutcome]]
) -> GradedDelivery:
    """Grade a delivery and decide whether to quarantine it.

        fail  - any CRITICAL failed, or rejected rows exceed the threshold
        warn  - any WARNING failed
        pass  - otherwise

    Quarantine is a separate decision driven only by CRITICAL failures. A
    delivery can be graded `fail` on rejected-row volume while its surviving
    rows stay valid and countable; quarantine is for deliveries whose numbers as
    a whole cannot be believed — a duplicate file, a unit error, a missing week.
    """
    reasons: list[str] = []
    critical_failures = [
        chk for chk, outcome in results if outcome.status == FAIL and chk.severity == CRITICAL
    ]
    warning_failures = [
        chk for chk, outcome in results if outcome.status == FAIL and chk.severity == WARNING
    ]

    for chk in critical_failures:
        reasons.append(f"{chk.check_id} (critical)")
    for chk in warning_failures:
        reasons.append(f"{chk.check_id} (warning)")

    quarantined = bool(critical_failures)

    if critical_failures:
        status = HEALTH_FAIL
    elif ctx.rejected_pct > REJECTED_ROW_PCT_FAIL:
        status = HEALTH_FAIL
        reasons.append(
            f"{ctx.rejected_pct:.1f}% of records rejected "
            f"(threshold {REJECTED_ROW_PCT_FAIL}%)"
        )
    elif warning_failures:
        status = HEALTH_WARN
    else:
        status = HEALTH_PASS

    return GradedDelivery(
        results=results, status=status, quarantined=quarantined, reasons=reasons
    )
