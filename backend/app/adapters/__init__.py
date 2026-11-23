"""Adapter registry. A new platform is a module plus an entry in build_adapters."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.adapters.base import (
    CanonicalRow,
    FileParseError,
    NormalizeResult,
    PlatformAdapter,
    RawRecord,
    Reason,
    RowRejection,
    canonical_campaign_key,
    week_start_from_filename,
)
from app.adapters.google import GoogleAdapter
from app.adapters.linkedin import LinkedInAdapter
from app.adapters.meta import MetaAdapter

__all__ = [
    "CanonicalRow",
    "FileParseError",
    "NormalizeResult",
    "PlatformAdapter",
    "RawRecord",
    "Reason",
    "RowRejection",
    "build_adapters",
    "adapter_for",
    "canonical_campaign_key",
    "week_start_from_filename",
]


def build_adapters(rates: dict[str, Decimal]) -> tuple[PlatformAdapter, ...]:
    return (MetaAdapter(), GoogleAdapter(rates), LinkedInAdapter(rates))


def adapter_for(path: Path, adapters: tuple[PlatformAdapter, ...]) -> PlatformAdapter | None:
    """None for an unrecognised filename, so the caller can record it rather
    than crash or silently skip it."""
    name = path.name
    for adapter in adapters:
        if name.startswith(adapter.filename_prefix):
            return adapter
    return None
