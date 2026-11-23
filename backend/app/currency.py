"""Exchange rates from data/exchange_rates.json.

Rates are parsed as Decimal. Left to json's default, 1.08 becomes the binary
double 1.08000000000000007105..., and every converted row inherits the error.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from app.config import EXCHANGE_RATES_PATH


class UnknownCurrencyError(LookupError):
    """A delivery reported a currency finance supplied no rate for."""


def load_rates(path: Path | None = None) -> dict[str, Decimal]:
    """Currency -> USD multipliers: 1 unit of currency = N USD."""
    source = path or EXCHANGE_RATES_PATH
    with source.open(encoding="utf-8") as handle:
        payload = json.load(handle, parse_float=Decimal)

    rates: dict[str, Decimal] = {}
    for code, value in payload.get("rates", {}).items():
        # parse_float does not intercept ints, so "USD": 1 arrives untouched.
        # Via str() because Decimal(float) would silently carry the error.
        rates[code.upper()] = value if isinstance(value, Decimal) else Decimal(str(value))
    return rates


def rate_for(rates: dict[str, Decimal], currency: str | None) -> Decimal:
    """Raises UnknownCurrencyError, which adapters turn into a row rejection."""
    if not currency:
        raise UnknownCurrencyError("currency is missing")
    code = currency.strip().upper()
    if code not in rates:
        raise UnknownCurrencyError(f"no exchange rate supplied for {code!r}")
    return rates[code]
