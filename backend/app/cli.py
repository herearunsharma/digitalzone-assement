"""CLI entry point: `python -m app.cli`.

Separate from app.pipeline.ingest because running that module directly makes
Python import it twice — once as `app.pipeline.ingest` via the package __init__,
once as `__main__` — which would double-register every check.
"""

from __future__ import annotations

from app.pipeline.ingest import main

if __name__ == "__main__":
    main()
