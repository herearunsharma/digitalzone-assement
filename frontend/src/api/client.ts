/**
 * Thin fetch wrapper. One job beyond fetching: turn the API's problem+json
 * body into a thrown Error carrying the server's own `detail`, so every view
 * can render a real explanation instead of "Request failed".
 */

import type {
  CadenceResponse,
  DeliveryDetail,
  DeliverySummary,
  GroupBy,
  IngestionSummary,
  MetricsResponse,
  Platform,
  ProblemDetail,
} from "./types";

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly problem?: ProblemDetail,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, init);
  if (!response.ok) {
    let problem: ProblemDetail | undefined;
    try {
      problem = (await response.json()) as ProblemDetail;
    } catch {
      // Body was not JSON (proxy error, backend down). Fall through to the
      // generic message rather than masking the status code.
    }
    throw new ApiError(
      problem?.detail ?? `${response.status} ${response.statusText}`,
      response.status,
      problem,
    );
  }
  return (await response.json()) as T;
}

export interface MetricsQuery {
  platforms: Platform[];
  dateFrom: string;
  dateTo: string;
  groupBy: GroupBy;
  includeQuarantined: boolean;
}

/**
 * Builds the query string. Empty values are omitted entirely rather than sent
 * as `?date_from=` — an empty string is not a valid date and the API would
 * (correctly) reject it with a 422.
 */
export function metricsSearchParams(query: MetricsQuery): string {
  const params = new URLSearchParams();
  query.platforms.forEach((p) => params.append("platform", p));
  if (query.dateFrom) params.set("date_from", query.dateFrom);
  if (query.dateTo) params.set("date_to", query.dateTo);
  params.set("group_by", query.groupBy);
  if (query.includeQuarantined) params.set("include_quarantined", "true");
  return params.toString();
}

export const api = {
  metrics: (query: MetricsQuery) =>
    request<MetricsResponse>(`/metrics?${metricsSearchParams(query)}`),
  deliveries: () => request<DeliverySummary[]>("/deliveries"),
  delivery: (id: string) => request<DeliveryDetail>(`/deliveries/${id}`),
  cadence: () => request<CadenceResponse>("/deliveries/cadence"),
  runIngestion: () =>
    request<IngestionSummary>("/ingestions", { method: "POST" }),
};
