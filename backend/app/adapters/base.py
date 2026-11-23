"""Canonical row model and the adapter contract.

`normalize()` returns a rejection instead of raising so a single malformed row
cannot abort an ingestion run. Exceptions are reserved for file-level failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

MICROS_PER_USD = 1_000_000

_WEEK_IN_FILENAME = re.compile(r"(\d{4}-\d{2}-\d{2})")


class FileParseError(Exception):
    """File could not be read as its declared format. Invalidates the delivery."""


class Reason:
    """Rejection codes. Plain strings so adapters can add their own without
    touching a central enum, and so they serialise into the report unchanged."""

    MISSING_FIELD = "missing_field"
    DATE_UNPARSEABLE = "date_unparseable"
    DATE_INVALID = "date_invalid"
    NUMERIC_INVALID = "numeric_invalid"
    NUMERIC_NEGATIVE = "numeric_negative"
    CURRENCY_UNKNOWN = "currency_unknown"


@dataclass(frozen=True, slots=True)
class RawRecord:
    """Source record before interpretation. `line` is 1-based: the CSV line or
    JSON array index a human would use to find it in an editor."""

    line: int
    data: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CanonicalRow:
    platform: str
    campaign_raw: str
    campaign_key: str
    date: date
    spend_usd_micros: int
    impressions: int
    clicks: int
    source_line: int
    # Carried so a file-level check can spot a delivery that mixes date
    # representations; the adapter's format fallback would otherwise absorb it.
    date_format: str = ""


@dataclass(frozen=True, slots=True)
class RowRejection:
    line: int
    reason_code: str
    field: str
    value: str
    message: str


NormalizeResult = CanonicalRow | RowRejection


@runtime_checkable
class PlatformAdapter(Protocol):
    """Protocol rather than a base class: adapters inherit nothing, so adding a
    platform is a new module plus a registry entry."""

    platform: str
    filename_prefix: str
    file_extension: str
    expected_columns: tuple[str, ...]

    def parse(self, path: Path) -> list[RawRecord]:
        """Raises FileParseError if the file is unreadable as its format."""
        ...

    def normalize(self, raw: RawRecord) -> NormalizeResult: ...


def canonical_campaign_key(raw: str) -> str:
    """Identity used to merge spelling variants of the same campaign.

    split/join collapses internal whitespace as well as trimming; casefold is
    the Unicode-correct fold, unlike lower().
    """
    return " ".join(raw.split()).casefold()


def week_start_from_filename(path: Path) -> date | None:
    """None when the filename carries no ISO date — we don't guess a week."""
    match = _WEEK_IN_FILENAME.search(path.stem)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def require_text(raw: RawRecord, field: str) -> str | RowRejection:
    """Returns a stripped value. Callers needing the original source string for
    provenance must read raw.data themselves."""
    value = raw.data.get(field)
    if value is None:
        return RowRejection(
            line=raw.line,
            reason_code=Reason.MISSING_FIELD,
            field=field,
            value="",
            message=f"required field {field!r} is absent from the record",
        )
    text = str(value).strip()
    if not text:
        return RowRejection(
            line=raw.line,
            reason_code=Reason.MISSING_FIELD,
            field=field,
            value="",
            message=f"required field {field!r} is empty",
        )
    return text


def parse_count(raw: RawRecord, field: str) -> int | RowRejection:
    text = require_text(raw, field)
    if isinstance(text, RowRejection):
        return text
    try:
        value = int(Decimal(text))
    except (InvalidOperation, ValueError, ArithmeticError):
        return RowRejection(
            line=raw.line,
            reason_code=Reason.NUMERIC_INVALID,
            field=field,
            value=text,
            message=f"{field} is not a number: {text!r}",
        )
    if value < 0:
        return RowRejection(
            line=raw.line,
            reason_code=Reason.NUMERIC_NEGATIVE,
            field=field,
            value=text,
            message=f"{field} is negative: {value}",
        )
    return value


def to_micros(amount: Decimal, rate: Decimal) -> int:
    """ROUND_HALF_UP rather than Python's banker's rounding, which biases
    totals on .5 boundaries."""
    micros = amount * rate * MICROS_PER_USD
    return int(micros.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def parse_money_micros(
    raw: RawRecord, field: str, rate: Decimal, *, already_micros: bool = False
) -> int | RowRejection:
    """`already_micros` skips the dollar scaling for sources that report in
    millionths natively (Google), applying only the FX rate."""
    text = require_text(raw, field)
    if isinstance(text, RowRejection):
        return text
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return RowRejection(
            line=raw.line,
            reason_code=Reason.NUMERIC_INVALID,
            field=field,
            value=text,
            message=f"{field} is not a number: {text!r}",
        )
    if amount < 0:
        # A refund or an export bug, either way not something to silently fold
        # into a campaign total.
        return RowRejection(
            line=raw.line,
            reason_code=Reason.NUMERIC_NEGATIVE,
            field=field,
            value=text,
            message=f"{field} is negative: {amount}",
        )

    if already_micros:
        return int((amount * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return to_micros(amount, rate)
