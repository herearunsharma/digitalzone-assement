"""FastAPI application."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import deliveries, ingestions, metrics
from app.api.errors import register_error_handlers
from app.db import init_db
from app.quality import all_checks


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # So a fresh checkout serves empty results before the first ingestion
    # rather than 500-ing on a missing table.
    init_db()
    yield


app = FastAPI(
    title="Campaign Data Hub",
    version="0.1.0",
    description=(
        "Normalized campaign metrics and per-delivery data-quality reporting. "
        "Metrics exclude quarantined deliveries by default; pass "
        "include_quarantined=true to see them."
    ),
    lifespan=lifespan,
)

# Scoped to the Vite dev server rather than "*": permissive CORS defaults have
# a habit of reaching production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

register_error_handlers(app)
app.include_router(metrics.router)
app.include_router(deliveries.router)
app.include_router(ingestions.router)


@app.get("/api/v1/health", tags=["meta"], summary="Liveness probe")
def health() -> dict[str, object]:
    """Liveness only; does not touch the database. Distinct from the *data*
    health under /api/v1/deliveries. The check count lets an operator confirm
    which build of the quality engine is deployed.
    """
    return {"status": "ok", "registered_checks": len(all_checks())}
