/**
 * Display formatting, kept in one place so a number never renders two
 * different ways in two components.
 */

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const usdPrecise = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 3,
  maximumFractionDigits: 3,
});

const count = new Intl.NumberFormat("en-US");

export const formatUsd = (value: number) => usd.format(value);
export const formatCount = (value: number) => count.format(value);

/**
 * The null contract, rendered.
 *
 * CTR is null when impressions is 0; CPC is null when clicks is 0. Both render
 * as an em dash. Showing "0.00%" or "$0.00" would assert something false — that
 * the traffic converted at zero, or that clicks were free — and would sort
 * misleadingly in a "cheapest CPC" view.
 */
export const formatCtr = (value: number | null) =>
  value === null ? "—" : `${(value * 100).toFixed(2)}%`;

export const formatCpc = (value: number | null) =>
  value === null ? "—" : usdPrecise.format(value);

export const formatBytes = (value: number | null) =>
  value === null ? "—" : `${(value / 1024).toFixed(1)} KB`;

export const formatDate = (iso: string) => iso.slice(0, 10);
