"""Storage model.

delivery holds one row per expected slot, including slots that never arrived;
metric_fact the normalized dataset; check_result the quality report;
ingestion_run the history, kept apart so the fact tables stay byte-stable
between runs.

Money is `spend_usd_micros: int`, never a float — floats drift under
accumulation, and Google reports micros natively anyway. Conversion to dollars
happens once, at the API boundary.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Delivery(Base):
    """A (platform, week) slot we expected to receive.

    A row exists even when no file arrived (`is_present = False`), since a
    missing delivery still has to show up in the health view.
    """

    __tablename__ = "delivery"

    # sha256 of the path relative to the deliveries directory. Path-derived, so
    # a corrected re-send under the same name replaces the previous data rather
    # than double-counting; content_sha256 below answers the different question
    # of whether two files hold the same bytes.
    delivery_id: Mapped[str] = mapped_column(String(16), primary_key=True)

    platform: Mapped[str] = mapped_column(String(32), index=True)
    week_start: Mapped[date] = mapped_column(Date, index=True)

    # Relative, so the id and this column are stable across checkout locations.
    source_path: Mapped[str] = mapped_column(String(512))

    # NULL for a slot whose file never arrived.
    content_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer)

    is_present: Mapped[bool] = mapped_column(Boolean, default=True)

    # "pass" | "warn" | "fail"
    status: Mapped[str] = mapped_column(String(8), index=True)

    # Set when a CRITICAL defect makes the delivery's numbers untrustworthy.
    # Facts stay stored and inspectable but are excluded from aggregation by
    # default — flagged rather than corrected, since rewriting a source's
    # numbers on a guess is how a plausible wrong answer enters unmarked.
    quarantined: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    rows_parsed: Mapped[int] = mapped_column(Integer, default=0)
    rows_accepted: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)

    ingested_at: Mapped[datetime] = mapped_column(DateTime)

    facts: Mapped[list["MetricFact"]] = relationship(
        back_populates="delivery", cascade="all, delete-orphan"
    )
    checks: Mapped[list["CheckResult"]] = relationship(
        back_populates="delivery", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # A resend has its own path, so it legitimately occupies its own row and
        # is caught by the duplicate-content check rather than this constraint.
        UniqueConstraint("platform", "week_start", "source_path", name="uq_delivery_slot"),
    )


class MetricFact(Base):
    """One normalized (platform, campaign, date) observation."""

    __tablename__ = "metric_fact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    delivery_id: Mapped[str] = mapped_column(
        ForeignKey("delivery.delivery_id", ondelete="CASCADE"), index=True
    )

    platform: Mapped[str] = mapped_column(String(32), index=True)

    # Stripped and casefolded; merges spelling variants into one campaign.
    campaign_key: Mapped[str] = mapped_column(String(255), index=True)
    campaign_name: Mapped[str] = mapped_column(String(255))
    # Untouched source string. Kept so naming defects stay detectable and any
    # stored figure can be traced to what the platform actually sent.
    campaign_raw: Mapped[str] = mapped_column(String(255))

    date: Mapped[date] = mapped_column(Date, index=True)

    spend_usd_micros: Mapped[int] = mapped_column(Integer)
    impressions: Mapped[int] = mapped_column(Integer)
    clicks: Mapped[int] = mapped_column(Integer)

    # 1-based CSV line or JSON array index in the source file.
    source_line: Mapped[int] = mapped_column(Integer)

    delivery: Mapped[Delivery] = relationship(back_populates="facts")

    __table_args__ = (
        # At most one observation per campaign per day per delivery. Sources do
        # violate this; dedup runs before insert, so the constraint is a net
        # that surfaces a dedup regression loudly rather than double-counting.
        UniqueConstraint("delivery_id", "campaign_key", "date", name="uq_fact_grain"),
        # The API's hot path: filter by platform and date range.
        Index("ix_fact_platform_date", "platform", "date"),
    )


class CheckResult(Base):
    """One check's outcome for one delivery — the structured quality report (FR-2)."""

    __tablename__ = "check_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    delivery_id: Mapped[str] = mapped_column(
        ForeignKey("delivery.delivery_id", ondelete="CASCADE"), index=True
    )

    check_id: Mapped[str] = mapped_column(String(64), index=True)  # e.g. "file.spend_magnitude"
    level: Mapped[str] = mapped_column(String(16))     # "row" | "file" | "delivery"
    severity: Mapped[str] = mapped_column(String(16))  # "critical" | "warning" | "info"
    status: Mapped[str] = mapped_column(String(8))     # "pass" | "fail" | "skip"

    # Actionable without opening the details blob.
    message: Mapped[str] = mapped_column(String(512))

    # Counts, thresholds, offending lines — what makes the report answer "how
    # badly". JSON because the shape differs per check, and columns would mean a
    # migration every time someone adds one.
    details: Mapped[dict] = mapped_column(JSON, default=dict)

    delivery: Mapped[Delivery] = relationship(back_populates="checks")

    __table_args__ = (
        UniqueConstraint("delivery_id", "check_id", name="uq_check_per_delivery"),
    )


class IngestionRun(Base):
    """Run history.

    Separate table because run timestamps are the only thing that legitimately
    changes between two identical ingestions; keeping them out of the fact
    tables lets the idempotency test assert exact equality.
    """

    __tablename__ = "ingestion_run"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    deliveries_processed: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    warned: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    rows_accepted: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
