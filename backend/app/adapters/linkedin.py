"""LinkedIn Ads: JSON array, epoch-millisecond dates, EUR spend as a string."""

from __future__ import annotations

import json
from datetime import UTC, datetime
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


class LinkedInAdapter:
    platform = "linkedin"
    filename_prefix = "linkedin_ads_"
    file_extension = "json"
    expected_columns = ("campaign", "date_ts", "spend", "impressions", "clicks")

    def __init__(self, rates: dict[str, Decimal]) -> None:
        self._rates = rates

    def parse(self, path: Path) -> list[RawRecord]:
        try:
            with path.open(encoding="utf-8") as handle:
                # Guards against an amount arriving as a bare JSON number
                # rather than the usual string.
                payload = json.load(handle, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise FileParseError(f"malformed JSON: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise FileParseError(f"file is not valid UTF-8: {exc}") from exc

        if not isinstance(payload, list):
            raise FileParseError(
                f"expected a JSON array of records, got {type(payload).__name__}"
            )

        records: list[RawRecord] = []
        for index, item in enumerate(payload, start=1):
            if not isinstance(item, dict):
                # Row-level defect, not file-level: wrap it so normalize can
                # reject it individually and the rest of the file still lands.
                records.append(RawRecord(line=index, data={"__invalid__": item}))
                continue
            records.append(RawRecord(line=index, data=item))
        return records

    def normalize(self, raw: RawRecord) -> NormalizeResult:
        if "__invalid__" in raw.data:
            return RowRejection(
                line=raw.line,
                reason_code=Reason.MISSING_FIELD,
                field="<record>",
                value=str(raw.data["__invalid__"])[:120],
                message="record is not a JSON object",
            )

        campaign = require_text(raw, "campaign")
        if isinstance(campaign, RowRejection):
            return campaign
        campaign_raw = str(raw.data["campaign"])

        ts = raw.data.get("date_ts")
        if ts is None:
            return RowRejection(
                line=raw.line,
                reason_code=Reason.MISSING_FIELD,
                field="date_ts",
                value="",
                message="required field 'date_ts' is absent from the record",
            )
        try:
            # Pinned to UTC: these instants are midnight UTC, so a local-time
            # conversion would shift dates by a day west of the meridian and
            # make the same code produce different numbers per machine.
            parsed_date = datetime.fromtimestamp(int(ts) // 1000, tz=UTC).date()
        except (TypeError, ValueError, OverflowError, OSError):
            return RowRejection(
                line=raw.line,
                reason_code=Reason.DATE_UNPARSEABLE,
                field="date_ts",
                value=str(ts),
                message=f"date_ts {ts!r} is not a usable epoch-millisecond timestamp",
            )

        spend_obj = raw.data.get("spend")
        if not isinstance(spend_obj, dict):
            return RowRejection(
                line=raw.line,
                reason_code=Reason.MISSING_FIELD,
                field="spend",
                value=str(spend_obj or ""),
                message="required field 'spend' is absent or not an object",
            )

        try:
            rate = rate_for(self._rates, spend_obj.get("currency"))
        except UnknownCurrencyError as exc:
            return RowRejection(
                line=raw.line,
                reason_code=Reason.CURRENCY_UNKNOWN,
                field="spend.currency",
                value=str(spend_obj.get("currency") or ""),
                message=str(exc),
            )

        # Flattened so the shared helper can name the field path in a rejection.
        spend_record = RawRecord(line=raw.line, data={"spend.amount": spend_obj.get("amount")})
        spend = parse_money_micros(spend_record, "spend.amount", rate)
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
            date_format="epoch_ms",
        )
