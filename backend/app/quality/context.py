"""Input to every check.

One context type for all three levels: row checks read `rejections`, file checks
read `rows`, delivery checks reach through `run` to see siblings. The shared
signature is what lets the runner treat them uniformly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.adapters import CanonicalRow, RowRejection
from app.config import EXPECTED_CAMPAIGN_COUNT, expected_dates


@dataclass
class DeliveryContext:
    delivery_id: str
    platform: str
    week_start: date

    source_path: str
    path: Path | None  # None when the delivery never arrived

    content_sha256: str | None
    size_bytes: int | None
    is_present: bool

    header: tuple[str, ...] | None
    expected_columns: tuple[str, ...]

    rows: list[CanonicalRow] = field(default_factory=list)
    rejections: list[RowRejection] = field(default_factory=list)

    parse_error: str | None = None

    # Assigned by RunContext; cross-delivery checks need it.
    run: "RunContext | None" = None

    @property
    def is_broken(self) -> bool:
        return (not self.is_present) or self.parse_error is not None

    @property
    def expected_dates(self) -> list[date]:
        return expected_dates(self.week_start)

    @property
    def expected_campaign_count(self) -> int:
        return EXPECTED_CAMPAIGN_COUNT.get(self.platform, 0)

    @property
    def expected_row_count(self) -> int:
        return self.expected_campaign_count * len(self.expected_dates)

    @property
    def records_seen(self) -> int:
        return len(self.rows) + len(self.rejections)

    @property
    def rejected_pct(self) -> float:
        if self.records_seen == 0:
            return 0.0
        return 100.0 * len(self.rejections) / self.records_seen


@dataclass
class RunContext:
    """Read-only view of every delivery in a run.

    Cross-delivery checks consult this rather than the database, so a check's
    verdict cannot depend on how much has been written so far.
    """

    deliveries: list[DeliveryContext] = field(default_factory=list)

    def add(self, ctx: DeliveryContext) -> DeliveryContext:
        ctx.run = self
        self.deliveries.append(ctx)
        return ctx

    def siblings(self, ctx: DeliveryContext) -> list[DeliveryContext]:
        return [
            other
            for other in self.deliveries
            if other.platform == ctx.platform and other.delivery_id != ctx.delivery_id
        ]

    def first_with_hash(self, sha: str) -> DeliveryContext | None:
        """First by insertion order, which the pipeline fills in sorted path
        order — so byte-identical files always resolve the same way and a re-run
        cannot flip which one is treated as the duplicate."""
        for other in self.deliveries:
            if other.content_sha256 == sha:
                return other
        return None
