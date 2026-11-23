"""Per-platform normalization rules."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.adapters import CanonicalRow, RawRecord, RowRejection, canonical_campaign_key
from app.adapters.base import Reason
from app.adapters.google import GoogleAdapter
from app.adapters.linkedin import LinkedInAdapter
from app.adapters.meta import MetaAdapter
from app.currency import UnknownCurrencyError, load_rates, rate_for

RATES = {"USD": Decimal("1.0"), "EUR": Decimal("1.08")}


def rec(line: int, **data) -> RawRecord:
    return RawRecord(line=line, data=data)


# --- Currency ---------------------------------------------------------------


def test_rates_load_as_decimal_not_float():
    """As a float, 1.08 becomes 1.08000000000000007105... and every converted
    row carries the error into the stored total."""
    rates = load_rates()
    assert str(rates["EUR"]) == "1.08"
    assert isinstance(rates["EUR"], Decimal)


def test_unknown_currency_raises_typed_error():
    with pytest.raises(UnknownCurrencyError):
        rate_for(RATES, "GBP")


# --- Meta -------------------------------------------------------------------


def test_meta_parses_us_date_format():
    row = MetaAdapter().normalize(
        rec(2, campaign_name="Summer Sale", date="06/07/2026",
            spend_usd="200.08", impressions="37215", clicks="574")
    )
    assert isinstance(row, CanonicalRow)
    assert row.date == date(2026, 6, 7)       # month-first, not 7 June -> 6 July
    assert row.spend_usd_micros == 200_080_000
    assert row.date_format == "%m/%d/%Y"


def test_meta_falls_back_to_iso_date():
    row = MetaAdapter().normalize(
        rec(9, campaign_name="Brand Awareness Q2", date="2026-06-01",
            spend_usd="139.02", impressions="36375", clicks="443")
    )
    assert isinstance(row, CanonicalRow)
    assert row.date == date(2026, 6, 1)
    assert row.date_format == "%Y-%m-%d"


def test_meta_rejects_impossible_calendar_date():
    """A naive parser rolls 06/31 over to 1 July, moving a day's spend into the
    next month."""
    out = MetaAdapter().normalize(
        rec(23, campaign_name="App Install Push", date="06/31/2026",
            spend_usd="241.02", impressions="92138", clicks="2667")
    )
    assert isinstance(out, RowRejection)
    assert out.reason_code == Reason.DATE_INVALID
    assert out.line == 23


def test_meta_rejects_negative_spend():
    out = MetaAdapter().normalize(
        rec(8, campaign_name="Summer Sale", date="06/28/2026",
            spend_usd="-111.41", impressions="25217", clicks="425")
    )
    assert isinstance(out, RowRejection)
    assert out.reason_code == Reason.NUMERIC_NEGATIVE


def test_meta_rejects_empty_clicks():
    out = MetaAdapter().normalize(
        rec(11, campaign_name="Brand Awareness Q2", date="06/17/2026",
            spend_usd="112.25", impressions="35929", clicks="")
    )
    assert isinstance(out, RowRejection)
    assert out.reason_code == Reason.MISSING_FIELD
    assert out.field == "clicks"


def test_meta_preserves_raw_campaign_while_merging_the_key():
    """campaign_raw is the evidence the naming check needs to spot whitespace
    variants, so it must stay untouched."""
    row = MetaAdapter().normalize(
        rec(5, campaign_name="Brand Awareness Q2  ", date="06/17/2026",
            spend_usd="10.00", impressions="100", clicks="5")
    )
    assert isinstance(row, CanonicalRow)
    assert row.campaign_raw == "Brand Awareness Q2  "
    assert row.campaign_key == "brand awareness q2"


# --- Google -----------------------------------------------------------------


def test_google_micros_pass_through_unscaled():
    """Already in millionths; scaling again inflates spend a million-fold."""
    row = GoogleAdapter(RATES).normalize(
        rec(2, **{"Campaign": "Search - Brand", "Day": "2026-06-07",
                  "Cost (micros)": "117410000", "Currency": "USD",
                  "Impr.": "37355", "Clicks": "779"})
    )
    assert isinstance(row, CanonicalRow)
    assert row.spend_usd_micros == 117_410_000  # $117.41


def test_google_rejects_unknown_currency_rather_than_assuming_usd():
    out = GoogleAdapter(RATES).normalize(
        rec(2, **{"Campaign": "Search - Brand", "Day": "2026-06-07",
                  "Cost (micros)": "117410000", "Currency": "JPY",
                  "Impr.": "37355", "Clicks": "779"})
    )
    assert isinstance(out, RowRejection)
    assert out.reason_code == Reason.CURRENCY_UNKNOWN


# --- LinkedIn ---------------------------------------------------------------


def test_linkedin_converts_epoch_ms_in_utc():
    """Instants are midnight UTC; a local-time conversion shifts dates by a day
    west of the meridian."""
    row = LinkedInAdapter(RATES).normalize(
        rec(1, campaign="ABM Tier 1", date_ts=1780272000000,
            spend={"amount": "92.37", "currency": "EUR"},
            impressions=41097, clicks=1283)
    )
    assert isinstance(row, CanonicalRow)
    assert row.date == date(2026, 6, 1)


def test_linkedin_converts_eur_exactly():
    """92.37 EUR x 1.08 = 99.7596 USD exactly, via Decimal."""
    row = LinkedInAdapter(RATES).normalize(
        rec(1, campaign="ABM Tier 1", date_ts=1780272000000,
            spend={"amount": "92.37", "currency": "EUR"},
            impressions=41097, clicks=1283)
    )
    assert isinstance(row, CanonicalRow)
    assert row.spend_usd_micros == 99_759_600
    assert Decimal(row.spend_usd_micros) / 1_000_000 == Decimal("99.7596")


def test_linkedin_rejects_record_with_clicks_key_absent():
    """Absent, not null."""
    out = LinkedInAdapter(RATES).normalize(
        rec(4, campaign="ABM Tier 1", date_ts=1781136000000,
            spend={"amount": "157.13", "currency": "EUR"}, impressions=38983)
    )
    assert isinstance(out, RowRejection)
    assert out.reason_code == Reason.MISSING_FIELD
    assert out.field == "clicks"


# --- Canonicalisation -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Brand Awareness Q2", "brand awareness q2"),
        ("brand awareness q2", "brand awareness q2"),
        ("Brand Awareness Q2  ", "brand awareness q2"),
        ("  Brand  Awareness   Q2 ", "brand awareness q2"),
        ("Retargeting - US", "retargeting - us"),
    ],
)
def test_campaign_key_merges_case_and_whitespace_variants(raw, expected):
    assert canonical_campaign_key(raw) == expected


def test_campaign_key_does_not_merge_genuinely_different_campaigns():
    assert canonical_campaign_key("Search - Brand") != canonical_campaign_key(
        "Search - Generic"
    )
