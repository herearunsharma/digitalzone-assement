"""Delivery discovery and identity."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.adapters import PlatformAdapter, adapter_for, week_start_from_filename

_HASH_CHUNK = 1 << 20


def delivery_id_for(relative_path: str) -> str:
    """Stable id for a delivery slot, derived from the path rather than content.

    Path identity means a corrected re-send under the same name replaces the
    previous data instead of landing alongside it and double-counting. The
    content hash answers a different question and lives in its own column.

    16 hex chars: 64 bits over a few hundred filenames, short enough for a URL.
    """
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    path: Path
    relative: str
    platform: str
    week_start: date
    adapter: PlatformAdapter
    sha256: str
    size_bytes: int

    @property
    def delivery_id(self) -> str:
        return delivery_id_for(self.relative)


@dataclass(frozen=True, slots=True)
class UnrecognisedFile:
    relative: str
    reason: str


def discover(
    deliveries_dir: Path, adapters: tuple[PlatformAdapter, ...]
) -> tuple[list[DiscoveredFile], list[UnrecognisedFile]]:
    """Enumerate delivery files in a reproducible order.

    The sort is load-bearing. Byte-identical files resolve by processing order —
    first wins, the rest are flagged as duplicates — and filesystem iteration
    order is not stable across machines or runs.
    """
    discovered: list[DiscoveredFile] = []
    unrecognised: list[UnrecognisedFile] = []

    for path in sorted(deliveries_dir.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue

        relative = path.name
        adapter = adapter_for(path, adapters)
        if adapter is None:
            unrecognised.append(
                UnrecognisedFile(relative, "no adapter matches this filename prefix")
            )
            continue

        week_start = week_start_from_filename(path)
        if week_start is None:
            # Guessing a week would corrupt cadence reporting.
            unrecognised.append(
                UnrecognisedFile(relative, "filename carries no ISO week-start date")
            )
            continue

        discovered.append(
            DiscoveredFile(
                path=path,
                relative=relative,
                platform=adapter.platform,
                week_start=week_start,
                adapter=adapter,
                sha256=sha256_of(path),
                size_bytes=path.stat().st_size,
            )
        )

    return discovered, unrecognised


def expected_filename(adapter: PlatformAdapter, week_start: date) -> str:
    return f"{adapter.filename_prefix}{week_start.isoformat()}.{adapter.file_extension}"
