"""Quality engine. Importing this package imports `checks`, populating the registry."""

from __future__ import annotations

from app.quality import checks as _checks  # noqa: F401 - import for side effect
from app.quality.context import DeliveryContext, RunContext
from app.quality.dedup import partition_duplicates
from app.quality.registry import (
    CRITICAL,
    DELIVERY,
    FAIL,
    FILE,
    INFO,
    PASS,
    ROW,
    SKIP,
    WARNING,
    Check,
    CheckOutcome,
    all_checks,
    check,
)
from app.quality.runner import (
    HEALTH_FAIL,
    HEALTH_PASS,
    HEALTH_WARN,
    GradedDelivery,
    classify,
    run_checks,
)

__all__ = [
    "CRITICAL",
    "DELIVERY",
    "FAIL",
    "FILE",
    "HEALTH_FAIL",
    "HEALTH_PASS",
    "HEALTH_WARN",
    "INFO",
    "PASS",
    "ROW",
    "SKIP",
    "WARNING",
    "Check",
    "CheckOutcome",
    "DeliveryContext",
    "GradedDelivery",
    "RunContext",
    "all_checks",
    "check",
    "classify",
    "partition_duplicates",
    "run_checks",
]
