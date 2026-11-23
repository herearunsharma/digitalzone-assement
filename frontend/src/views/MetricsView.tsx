import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type MetricsQuery } from "../api/client";
import type { GroupBy, MetricGroup, Platform } from "../api/types";
import { formatCount, formatCpc, formatCtr, formatUsd } from "../format";

const PLATFORMS: Platform[] = ["meta", "google", "linkedin"];

/** Columns the table can sort by. `null` values always sort last. */
type SortKey = "campaign" | "spend_usd" | "impressions" | "clicks" | "ctr" | "cpc";

export default function MetricsView() {
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [groupBy, setGroupBy] = useState<GroupBy>("campaign");
  const [includeQuarantined, setIncludeQuarantined] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("spend_usd");
  const [ascending, setAscending] = useState(false);

  const query: MetricsQuery = {
    platforms,
    dateFrom,
    dateTo,
    groupBy,
    includeQuarantined,
  };

  /**
   * The query key IS the filter state. Change a filter and TanStack Query
   * refetches; go back to a previous filter combination and it serves the
   * cached result. No manual effect wiring, no stale-request races.
   */
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["metrics", query],
    queryFn: () => api.metrics(query),
  });

  /**
   * Sorting is client-side on the returned groups. There are tens of rows, so a
   * round trip per header click would add latency for no benefit. If this grew
   * to thousands of campaigns, sorting and pagination would move server-side —
   * noted in the README.
   */
  const rows = useMemo(() => {
    const groups: MetricGroup[] = data?.groups ? [...data.groups] : [];
    const direction = ascending ? 1 : -1;
    groups.sort((a, b) => {
      if (sortKey === "campaign") {
        const left = a.campaign ?? a.platform;
        const right = b.campaign ?? b.platform;
        return left.localeCompare(right) * direction;
      }
      const left = a[sortKey];
      const right = b[sortKey];
      // null (undefined CTR/CPC) always sorts to the bottom, in both
      // directions — an undefined value is not "the smallest", it is absent.
      if (left === null && right === null) return 0;
      if (left === null) return 1;
      if (right === null) return -1;
      return (left - right) * direction;
    });
    return groups;
  }, [data, sortKey, ascending]);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setAscending((previous) => !previous);
    } else {
      setSortKey(key);
      setAscending(false);
    }
  }

  function togglePlatform(platform: Platform) {
    setPlatforms((current) =>
      current.includes(platform)
        ? current.filter((p) => p !== platform)
        : [...current, platform],
    );
  }

  const sortIndicator = (key: SortKey) =>
    key === sortKey ? (ascending ? " ▲" : " ▼") : "";

  return (
    <section>
      <div className="filters" role="group" aria-label="Metric filters">
        <fieldset>
          <legend>Platform</legend>
          {PLATFORMS.map((platform) => (
            <label key={platform} className="checkbox">
              <input
                type="checkbox"
                checked={platforms.includes(platform)}
                onChange={() => togglePlatform(platform)}
              />
              {platform}
            </label>
          ))}
          {platforms.length === 0 && <span className="hint">all platforms</span>}
        </fieldset>

        <fieldset>
          <legend>Date range</legend>
          <label className="field">
            From
            <input
              type="date"
              value={dateFrom}
              onChange={(e) => setDateFrom(e.target.value)}
            />
          </label>
          <label className="field">
            To
            <input
              type="date"
              value={dateTo}
              onChange={(e) => setDateTo(e.target.value)}
            />
          </label>
        </fieldset>

        <fieldset>
          <legend>Group by</legend>
          <label className="field">
            <select
              value={groupBy}
              onChange={(e) => setGroupBy(e.target.value as GroupBy)}
            >
              <option value="campaign">Campaign</option>
              <option value="platform">Platform</option>
            </select>
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={includeQuarantined}
              onChange={(e) => setIncludeQuarantined(e.target.checked)}
            />
            include quarantined
          </label>
        </fieldset>

        <button
          type="button"
          className="btn-ghost"
          onClick={() => {
            setPlatforms([]);
            setDateFrom("");
            setDateTo("");
            setIncludeQuarantined(false);
          }}
        >
          Reset
        </button>
      </div>

      {isError && <p className="error-banner">{(error as Error).message}</p>}
      {isPending && <p className="muted">Loading…</p>}

      {data && (
        <>
          {/*
            Totals come straight from the API response — they are NOT summed
            from the table rows. If the client recomputed them it could disagree
            with the API, and "nobody trusts the aggregated numbers" is the
            problem this project exists to fix.
          */}
          <div className="totals">
            <Total label="Spend (USD)" value={formatUsd(data.totals.spend_usd)} />
            <Total label="Impressions" value={formatCount(data.totals.impressions)} />
            <Total label="Clicks" value={formatCount(data.totals.clicks)} />
            <Total label="CTR" value={formatCtr(data.totals.ctr)} />
            <Total label="CPC" value={formatCpc(data.totals.cpc)} />
          </div>

          <p className="provenance">
            From {data.contributing_deliveries} deliver
            {data.contributing_deliveries === 1 ? "y" : "ies"}.
            {data.excluded_quarantined_deliveries > 0 && (
              <>
                {" "}
                <strong>
                  {data.excluded_quarantined_deliveries} quarantined deliver
                  {data.excluded_quarantined_deliveries === 1 ? "y" : "ies"}{" "}
                  excluded
                </strong>{" "}
                — see Data health for why.
              </>
            )}
          </p>

          <table className="data-table">
            <thead>
              <tr>
                <th>
                  <button type="button" onClick={() => toggleSort("campaign")}>
                    {groupBy === "campaign" ? "Campaign" : "Platform"}
                    {sortIndicator("campaign")}
                  </button>
                </th>
                {groupBy === "campaign" && <th>Platform</th>}
                <th className="numeric">
                  <button type="button" onClick={() => toggleSort("spend_usd")}>
                    Spend{sortIndicator("spend_usd")}
                  </button>
                </th>
                <th className="numeric">
                  <button type="button" onClick={() => toggleSort("impressions")}>
                    Impressions{sortIndicator("impressions")}
                  </button>
                </th>
                <th className="numeric">
                  <button type="button" onClick={() => toggleSort("clicks")}>
                    Clicks{sortIndicator("clicks")}
                  </button>
                </th>
                <th className="numeric">
                  <button type="button" onClick={() => toggleSort("ctr")}>
                    CTR{sortIndicator("ctr")}
                  </button>
                </th>
                <th className="numeric">
                  <button type="button" onClick={() => toggleSort("cpc")}>
                    CPC{sortIndicator("cpc")}
                  </button>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <td>{row.campaign ?? row.platform}</td>
                  {groupBy === "campaign" && (
                    <td>
                      <span className="pill">{row.platform}</span>
                    </td>
                  )}
                  <td className="numeric">{formatUsd(row.spend_usd)}</td>
                  <td className="numeric">{formatCount(row.impressions)}</td>
                  <td className="numeric">{formatCount(row.clicks)}</td>
                  <td className="numeric">{formatCtr(row.ctr)}</td>
                  <td className="numeric">{formatCpc(row.cpc)}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={7} className="muted">
                    No metrics match these filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

function Total({ label, value }: { label: string; value: string }) {
  return (
    <div className="total-card">
      <span className="total-label">{label}</span>
      <span className="total-value">{value}</span>
    </div>
  );
}
