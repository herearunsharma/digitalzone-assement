"""Meta Ads: CSV, US-style dates, spend already in USD."""

from __future__ import annotations

import csv
from datetime import datetime
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


class MetaAdapter:
    platform = "meta"
    filename_prefix = "meta_ads_"
    file_extension = "csv"
    expected_columns = ("campaign_name", "date", "spend_usd", "impressions", "clicks")

    # Exports are predominantly month-first but occasionally emit ISO. The two
    # cannot collide, so order is a cost choice rather than a correctness one.
    #
    # Month-first is the right reading of an ambiguous value like 06/07/2026:
    # the filename declares a June week, and the data contains a literal
    # 06/31/2026 which is only coherent with month=06. row.date_in_window
    # polices the assumption if a future export flips convention.
    DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d")

    def parse(self, path: Path) -> list[RawRecord]:
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames is None:
                    raise FileParseError("file is empty — no header row")
                # start=2 so source_line matches the line a text editor shows.
                return [RawRecord(line=index, data=row) for index, row in enumerate(reader, start=2)]
        except UnicodeDecodeError as exc:
            raise FileParseError(f"file is not valid UTF-8: {exc}") from exc
        except csv.Error as exc:
            raise FileParseError(f"malformed CSV: {exc}") from exc

    def normalize(self, raw: RawRecord) -> NormalizeResult:
        campaign = require_text(raw, "campaign_name")
        if isinstance(campaign, RowRejection):
            return campaign
        # require_text strips; re-read the source string so whitespace variants
        # stay visible to the naming check and to the audit trail.
        campaign_raw = str(raw.data["campaign_name"])

        date_text = require_text(raw, "date")
        if isinstance(date_text, RowRejection):
            return date_text

        parsed_date = None
        matched_format = ""
        for fmt in self.DATE_FORMATS:
            try:
                parsed_date = datetime.strptime(date_text, fmt).date()
                matched_format = fmt
                break
            except ValueError:
                continue
        if parsed_date is None:
            # strptime rejects out-of-range days, so 06/31 lands here rather
            # than rolling over into July.
            return RowRejection(
                line=raw.line,
                reason_code=Reason.DATE_INVALID,
                field="date",
                value=date_text,
                message=(
                    f"date {date_text!r} is not a real calendar date in any "
                    f"accepted format {self.DATE_FORMATS}"
                ),
            )

        spend = parse_money_micros(raw, "spend_usd", Decimal(1))
        if isinstance(spend, RowRejection):
            return spend

        impressions = parse_count(raw, "impressions")
        if isinstance(impressions, RowRejection):
            return impressions

        clicks = parse_count(raw, "clicks")
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
            date_format=matched_format,
        )
