import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./api/client";
import MetricsView from "./views/MetricsView";
import HealthView from "./views/HealthView";

type Tab = "metrics" | "health";

export default function App() {
  const [tab, setTab] = useState<Tab>("metrics");
  const queryClient = useQueryClient();

  /**
   * Triggering ingestion is a mutation, not a query: it changes server state.
   * On success every cached query is invalidated, so both views re-fetch and
   * the UI can never show pre-ingestion numbers next to a "done" message.
   */
  const ingestion = useMutation({
    mutationFn: api.runIngestion,
    onSuccess: () => queryClient.invalidateQueries(),
  });

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>Campaign Data Hub</h1>
          <p className="subtitle">
            Normalized campaign metrics and per-delivery data quality.
          </p>
        </div>
        <div className="header-actions">
          <button
            type="button"
            className="btn-primary"
            onClick={() => ingestion.mutate()}
            disabled={ingestion.isPending}
          >
            {ingestion.isPending ? "Ingesting…" : "Run ingestion"}
          </button>
          {ingestion.isSuccess && (
            <span className="ingest-result">
              {ingestion.data.deliveries_processed} deliveries ·{" "}
              {ingestion.data.passed} pass / {ingestion.data.warned} warn /{" "}
              {ingestion.data.failed} fail · {ingestion.data.rows_accepted} rows
            </span>
          )}
          {ingestion.isError && (
            <span className="ingest-result error">
              {(ingestion.error as Error).message}
            </span>
          )}
        </div>
      </header>

      <nav className="tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "metrics"}
          className={tab === "metrics" ? "tab active" : "tab"}
          onClick={() => setTab("metrics")}
        >
          Campaign metrics
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "health"}
          className={tab === "health" ? "tab active" : "tab"}
          onClick={() => setTab("health")}
        >
          Data health
        </button>
      </nav>

      <main>{tab === "metrics" ? <MetricsView /> : <HealthView />}</main>
    </div>
  );
}
