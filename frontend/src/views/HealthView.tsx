import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { CheckResult, DeliverySummary, HealthStatus } from "../api/types";
import { formatBytes, formatCount, formatDate, formatUsd } from "../format";

export default function HealthView() {
  const [selected, setSelected] = useState<string | null>(null);

  const cadence = useQuery({ queryKey: ["cadence"], queryFn: api.cadence });
  const deliveries = useQuery({ queryKey: ["deliveries"], queryFn: api.deliveries });

  return (
    <section className="health">
      <h2>Delivery cadence</h2>
      <p className="muted">
        One cell per expected delivery. An empty cell is a delivery that never
        arrived.
      </p>

      {cadence.data && (
        <table className="cadence">
          <thead>
            <tr>
              <th />
              {cadence.data.weeks.map((week) => (
                <th key={week} className="numeric">
                  {formatDate(week)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {cadence.data.platforms.map((platform) => (
              <tr key={platform}>
                <th scope="row">{platform}</th>
                {cadence.data.weeks.map((week) => {
                  const cell = cadence.data.cells.find(
                    (c) => c.platform === platform && c.week_start === week,
                  );
                  const status = cell?.status ?? null;
                  const missing = !cell?.is_present;
                  return (
                    <td key={week} className="cadence-cell">
                      <button
                        type="button"
                        className={`cell ${statusClass(status)}`}
                        disabled={!cell?.delivery_id}
                        onClick={() =>
                          cell?.delivery_id && setSelected(cell.delivery_id)
                        }
                        title={`${platform} · week of ${formatDate(week)}`}
                      >
                        {/*
                          Never colour alone: a glyph carries the same meaning
                          for anyone who cannot distinguish the hues, and the
                          title attribute carries it for a screen reader.
                        */}
                        {missing ? "✕" : status === "pass" ? "✓" : status === "warn" ? "!" : "✕"}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="legend">
        <span className="cell pass">✓</span> pass
        <span className="cell warn">!</span> warn
        <span className="cell fail">✕</span> fail / missing
      </div>

      <h2>Deliveries</h2>
      {deliveries.isError && (
        <p className="error-banner">{(deliveries.error as Error).message}</p>
      )}
      {deliveries.isPending && <p className="muted">Loading…</p>}

      {deliveries.data && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Status</th>
              <th>Delivery</th>
              <th>Platform</th>
              <th>Week</th>
              <th className="numeric">Accepted</th>
              <th className="numeric">Rejected</th>
              <th className="numeric">Failed checks</th>
              <th>Counted?</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {deliveries.data.map((delivery) => (
              <DeliveryRow
                key={delivery.delivery_id}
                delivery={delivery}
                onSelect={() => setSelected(delivery.delivery_id)}
              />
            ))}
          </tbody>
        </table>
      )}

      {selected && (
        <DeliveryDetailPanel id={selected} onClose={() => setSelected(null)} />
      )}
    </section>
  );
}

function statusClass(status: HealthStatus | null): string {
  if (status === "pass") return "pass";
  if (status === "warn") return "warn";
  return "fail";
}

function DeliveryRow({
  delivery,
  onSelect,
}: {
  delivery: DeliverySummary;
  onSelect: () => void;
}) {
  return (
    <tr className={delivery.quarantined ? "row-quarantined" : undefined}>
      <td>
        <span className={`badge ${statusClass(delivery.status)}`}>
          {delivery.status}
        </span>
      </td>
      <td>
        <code>{delivery.source_path}</code>{" "}
        {!delivery.is_present && <span className="pill danger">never arrived</span>}
      </td>
      <td>{delivery.platform}</td>
      <td>{formatDate(delivery.week_start)}</td>
      <td className="numeric">{formatCount(delivery.rows_accepted)}</td>
      <td className="numeric">{delivery.rows_rejected || "—"}</td>
      <td className="numeric">
        {delivery.checks_failed || "—"}
        {/*
          A severity flag, not a second count. Rendering the number again here
          put two bare digits side by side ("1" next to "1 critical" reads as
          "11 critical"). The exact breakdown is in the report panel.
        */}
        {delivery.critical_failures > 0 && (
          <span className="pill danger">critical</span>
        )}
      </td>
      <td>
        {/*
          The most important column in the table: it says whether this
          delivery's numbers are inside the figures shown on the metrics tab.
        */}
        {delivery.quarantined ? (
          <span className="pill danger">excluded</span>
        ) : (
          <span className="pill ok">counted</span>
        )}
      </td>
      <td>
        <button type="button" className="btn-ghost" onClick={onSelect}>
          Report
        </button>
      </td>
    </tr>
  );
}

function DeliveryDetailPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["delivery", id],
    queryFn: () => api.delivery(id),
  });

  return (
    <aside className="panel" aria-label="Delivery quality report">
      <div className="panel-header">
        <h3>{data?.source_path ?? "Quality report"}</h3>
        <button type="button" className="btn-ghost" onClick={onClose}>
          Close
        </button>
      </div>

      {isPending && <p className="muted">Loading…</p>}
      {isError && <p className="error-banner">{(error as Error).message}</p>}

      {data && (
        <>
          <dl className="meta-grid">
            <div>
              <dt>Status</dt>
              <dd>
                <span className={`badge ${statusClass(data.status)}`}>
                  {data.status}
                </span>
                {data.quarantined && (
                  <span className="pill danger">excluded from totals</span>
                )}
              </dd>
            </div>
            <div>
              <dt>Rows</dt>
              <dd>
                {data.rows_accepted} accepted · {data.rows_rejected} rejected
              </dd>
            </div>
            <div>
              <dt>Spend in this delivery</dt>
              <dd>{formatUsd(data.spend_usd)}</dd>
            </div>
            <div>
              <dt>Size</dt>
              <dd>{formatBytes(data.size_bytes)}</dd>
            </div>
            <div>
              <dt>Content SHA-256</dt>
              <dd>
                <code>{data.content_sha256?.slice(0, 16) ?? "—"}</code>
              </dd>
            </div>
          </dl>

          <h4>Checks</h4>
          <ul className="check-list">
            {data.checks.map((check) => (
              <CheckRow key={check.check_id} check={check} />
            ))}
          </ul>
        </>
      )}
    </aside>
  );
}

function CheckRow({ check }: { check: CheckResult }) {
  // Failed checks start expanded — the whole point of opening this panel is to
  // read the failures, and making the reviewer click again to see them would
  // bury the answer.
  const [open, setOpen] = useState(check.status === "fail");
  const hasDetails = Object.keys(check.details).length > 0;

  return (
    <li className={`check ${check.status}`}>
      <div className="check-head">
        <span className={`badge ${check.status === "fail" ? "fail" : check.status === "skip" ? "skip" : "pass"}`}>
          {check.status}
        </span>
        <code className="check-id">{check.check_id}</code>
        <span className={`pill ${check.severity === "critical" ? "danger" : ""}`}>
          {check.severity}
        </span>
        <span className="check-level">{check.level}</span>
      </div>
      <p className="check-message">{check.message}</p>
      {hasDetails && (
        <>
          <button
            type="button"
            className="btn-ghost small"
            onClick={() => setOpen((v) => !v)}
          >
            {open ? "Hide evidence" : "Show evidence"}
          </button>
          {open && (
            <pre className="details">{JSON.stringify(check.details, null, 2)}</pre>
          )}
        </>
      )}
    </li>
  );
}
