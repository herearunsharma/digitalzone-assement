"""Google Ads: CSV, ISO dates, cost in micros, per-row currency."""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.adapters.base import (
    CanonicalRow,
    FileParseError,
    NormalizeResult,
    RawRecord,
    Reason,
    RowRejection,
    canonical_campaign_key,
    parse_count,
    parse_money_micros,
    require_text,
)
from app.currency import UnknownCurrencyError, rate_for


class GoogleAdapter:
    platform = "google"
    filename_prefix = "google_ads_"
    file_extension = "csv"
    expected_columns = ("Campaign", "Day", "Cost (micros)", "Currency", "Impr.", "Clicks")

    def __init__(self, rates: dict[str, Decimal]) -> None:
        # Injected so tests can supply a fixture table and so a broken rates
        # file surfaces at startup rather than mid-run.
        self._rates = rates

    def parse(self, path: Path) -> list[RawRecord]:
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames is None:
                    raise FileParseError("file is empty — no header row")
                return [RawRecord(line=index, data=row) for index, row in enumerate(reader, start=2)]
        except UnicodeDecodeError as exc:
            raise FileParseError(f"file is not valid UTF-8: {exc}") from exc
        except csv.Error as exc:
            raise FileParseError(f"malformed CSV: {exc}") from exc

    def normalize(self, raw: RawRecord) -> NormalizeResult:
        campaign = require_text(raw, "Campaign")
        if isinstance(campaign, RowRejection):
            return campaign
        campaign_raw = str(raw.data["Campaign"])

        date_text = require_text(raw, "Day")
        if isinstance(date_text, RowRejection):
            return date_text
        try:
            parsed_date = date.fromisoformat(date_text)
        except ValueError:
            return RowRejection(
                line=raw.line,
                reason_code=Reason.DATE_UNPARSEABLE,
                field="Day",
                value=date_text,
                message=f"date {date_text!r} is not ISO-8601 (YYYY-MM-DD)",
            )

        currency = raw.data.get("Currency")
        try:
            rate = rate_for(self._rates, currency)
        except UnknownCurrencyError as exc:
            # Defaulting to 1.0 would invent a USD figure we cannot support.
            return RowRejection(
                line=raw.line,
                reason_code=Reason.CURRENCY_UNKNOWN,
                field="Currency",
                value=str(currency or ""),
                message=str(exc),
            )

        spend = parse_money_micros(raw, "Cost (micros)", rate, already_micros=True)
        if isinstance(spend, RowRejection):
            return spend

        impressions = parse_count(raw, "Impr.")
        if isinstance(impressions, RowRejection):
            return impressions

        clicks = parse_count(raw, "Clicks")
        if isinstance(clicks, RowRejection):
            return clicks

        return CanonicalRow(
            platform=self.platform,
            campaign_raw=campaign_raw,
            campaign_key=canonical_campaign_key(campaign_raw),
            date=parsed_date,
            spend_usd_micros=spend,
            impressions=impressions,
            clicks=clicks,
            source_line=raw.line,
            date_format="%Y-%m-%d",
        )
