/**
 * Mirrors the Pydantic response models in backend/app/api/schemas.py.
 *
 * Hand-written rather than generated from the OpenAPI schema: the surface is
 * small, and a generated client would add a build step a reviewer has to run
 * before the app compiles. The trade-off — these can drift from the backend —
 * is noted in the README as something to replace with codegen as the API grows.
 *
 * Note `ctr` and `cpc` are `number | null`. The null is load-bearing: CPC is
 * undefined when clicks is 0, and the UI must render that as "—", never 0.
 */

export type Platform = "meta" | "google" | "linkedin";
export type HealthStatus = "pass" | "warn" | "fail";
export type GroupBy = "campaign" | "platform";

export interface Metrics {
  spend_usd: number;
  impressions: number;
  clicks: number;
  ctr: number | null;
  cpc: number | null;
}

export interface MetricGroup extends Metrics {
  key: string;
  platform: string;
  campaign: string | null;
}

export interface AppliedFilters {
  platform: string[] | null;
  date_from: string | null;
  date_to: string | null;
  group_by: GroupBy;
  include_quarantined: boolean;
}

export interface MetricsResponse {
  filters: AppliedFilters;
  totals: Metrics;
  groups: MetricGroup[];
  contributing_deliveries: number;
  excluded_quarantined_deliveries: number;
}

export interface CheckResult {
  check_id: string;
  level: "row" | "file" | "delivery";
  severity: "critical" | "warning" | "info";
  status: "pass" | "fail" | "skip";
  message: string;
  details: Record<string, unknown>;
}

export interface DeliverySummary {
  delivery_id: string;
  platform: string;
  week_start: string;
  source_path: string;
  is_present: boolean;
  status: HealthStatus;
  quarantined: boolean;
  rows_parsed: number;
  rows_accepted: number;
  rows_rejected: number;
  size_bytes: number | null;
  content_sha256: string | null;
  ingested_at: string;
  checks_failed: number;
  critical_failures: number;
}

export interface DeliveryDetail extends DeliverySummary {
  checks: CheckResult[];
  spend_usd: number;
  impressions: number;
  clicks: number;
}

export interface CadenceCell {
  platform: string;
  week_start: string;
  status: HealthStatus | null;
  delivery_id: string | null;
  is_present: boolean;
  quarantined: boolean;
}

export interface CadenceResponse {
  weeks: string[];
  platforms: string[];
  cells: CadenceCell[];
}

export interface IngestionSummary {
  run_id: string;
  duration_seconds: number;
  deliveries_processed: number;
  passed: number;
  warned: number;
  failed: number;
  quarantined: number;
  rows_accepted: number;
  rows_rejected: number;
  rows_deduplicated: number;
  unrecognised_files: string[];
}

/** RFC 9457 problem+json — the one error shape the API returns. */
export interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance?: string;
  errors?: { field: string; message: string }[];
}
