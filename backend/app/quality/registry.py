"""Check registry.

Adding a check is one decorated function; the runner iterates the registry and
knows nothing about any individual check.

    @check(id="file.my_check", level=FILE, severity=WARNING, description="...")
    def my_check(ctx: DeliveryContext) -> CheckOutcome:
        return ok("...") if ... else fail("...", offending=[...])
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from app.quality.context import DeliveryContext

ROW = "row"
FILE = "file"
DELIVERY = "delivery"

# CRITICAL triggers quarantine, so it means "this delivery's numbers cannot be
# trusted at all", not merely "something is wrong".
CRITICAL = "critical"
WARNING = "warning"
INFO = "info"

PASS = "pass"
FAIL = "fail"
SKIP = "skip"


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    status: str
    message: str
    # Counts, thresholds, offending lines. Must stay JSON-serialisable; dates
    # are stringified at construction.
    details: dict[str, Any] = field(default_factory=dict)


def ok(message: str, **details: Any) -> CheckOutcome:
    return CheckOutcome(status=PASS, message=message, details=details)


def fail(message: str, **details: Any) -> CheckOutcome:
    return CheckOutcome(status=FAIL, message=message, details=details)


def skip(message: str, **details: Any) -> CheckOutcome:
    """Reported rather than omitted: "could not evaluate" and "passed" are
    different facts, and conflating them makes the report lie."""
    return CheckOutcome(status=SKIP, message=message, details=details)


CheckFn = Callable[["DeliveryContext"], CheckOutcome]


@dataclass(frozen=True, slots=True)
class Check:
    check_id: str
    level: str
    severity: str
    description: str
    fn: CheckFn
    # Most checks are meaningless without a parsed file; the runner
    # short-circuits those centrally so individual checks need no guard.
    runs_on_broken_delivery: bool = False


REGISTRY: list[Check] = []


def check(
    *,
    id: str,
    level: str,
    severity: str,
    description: str,
    runs_on_broken_delivery: bool = False,
) -> Callable[[CheckFn], CheckFn]:
    def decorator(fn: CheckFn) -> CheckFn:
        if any(existing.check_id == id for existing in REGISTRY):
            # Duplicate ids collide on (delivery_id, check_id) and silently drop
            # one set of results. Fail at import instead.
            raise ValueError(f"duplicate check id {id!r}")
        REGISTRY.append(
            Check(
                check_id=id,
                level=level,
                severity=severity,
                description=description,
                fn=fn,
                runs_on_broken_delivery=runs_on_broken_delivery,
            )
        )
        return fn

    return decorator


def all_checks() -> tuple[Check, ...]:
    """Stable order, so reports are reproducible across runs and machines."""
    order = {ROW: 0, FILE: 1, DELIVERY: 2}
    return tuple(sorted(REGISTRY, key=lambda c: (order.get(c.level, 9), c.check_id)))
