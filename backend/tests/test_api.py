"""API behaviour and edge cases."""

from __future__ import annotations

from app.api.schemas import derive_metrics

# Established by auditing the sample deliveries by hand.
TRUSTED_TOTAL_SPEND = 48_709.02
NAIVE_TOTAL_SPEND = 535_221.71


# --- Derived metrics --------------------------------------------------------


def test_cpc_is_null_when_clicks_is_zero():
    """Not 0.0, which would assert the clicks were free and sort to the top of a
    cheapest-CPC view. Infinity is not valid JSON."""
    metrics = derive_metrics(spend_micros=50_000_000, impressions=1_000, clicks=0)
    assert metrics.cpc is None
    assert metrics.spend_usd == 50.0
    assert metrics.ctr == 0.0  # zero clicks over real impressions IS a real 0% CTR


def test_ctr_is_null_when_impressions_is_zero():
    metrics = derive_metrics(spend_micros=0, impressions=0, clicks=0)
    assert metrics.ctr is None
    assert metrics.cpc is None


def test_division_happens_once_at_the_boundary():
    """Integer micros in, rounded dollars out."""
    metrics = derive_metrics(spend_micros=99_759_600, impressions=41_097, clicks=1_283)
    assert metrics.spend_usd == 99.76
    assert metrics.cpc == round(99.7596 / 1283, 4)


# --- Metrics ----------------------------------------------------------------


def test_totals_exclude_quarantined_deliveries_by_default(client):
    body = client.get("/api/v1/metrics").json()
    assert body["totals"]["spend_usd"] == TRUSTED_TOTAL_SPEND
    assert body["excluded_quarantined_deliveries"] == 3


def test_include_quarantined_exposes_the_untrusted_figure(client):
    body = client.get("/api/v1/metrics?include_quarantined=true").json()
    assert body["totals"]["spend_usd"] == NAIVE_TOTAL_SPEND
    # Inflated by the cents file plus the double-counted resend.
    assert body["totals"]["spend_usd"] > TRUSTED_TOTAL_SPEND * 10


def test_totals_match_the_sum_of_groups(client):
    """Totals come from their own aggregate, not a sum over groups, but the two
    must still agree."""
    body = client.get("/api/v1/metrics?group_by=campaign").json()
    summed = round(sum(g["spend_usd"] for g in body["groups"]), 2)
    assert abs(summed - body["totals"]["spend_usd"]) < 0.01


def test_filters_narrow_the_totals(client):
    everything = client.get("/api/v1/metrics").json()["totals"]
    meta_only = client.get("/api/v1/metrics?platform=meta").json()["totals"]
    one_week = client.get(
        "/api/v1/metrics?platform=meta&date_from=2026-06-01&date_to=2026-06-07"
    ).json()["totals"]

    assert meta_only["spend_usd"] < everything["spend_usd"]
    assert one_week["spend_usd"] < meta_only["spend_usd"]
    assert one_week["spend_usd"] == 4452.87


def test_filters_are_echoed_back(client):
    body = client.get("/api/v1/metrics?platform=meta&group_by=platform").json()
    assert body["filters"]["platform"] == ["meta"]
    assert body["filters"]["group_by"] == "platform"
    assert body["filters"]["include_quarantined"] is False


def test_daily_metrics_carry_provenance(client):
    """Every row names the delivery and source line it came from."""
    rows = client.get("/api/v1/metrics/daily?platform=meta&limit=5").json()
    assert rows
    for row in rows:
        assert row["delivery_id"]
        assert row["source_path"].startswith("meta_ads_")
        assert row["source_line"] >= 2  # line 1 is the CSV header


# --- Data health ------------------------------------------------------------


def test_delivery_list_includes_the_delivery_that_never_arrived(client):
    deliveries = client.get("/api/v1/deliveries").json()
    assert len(deliveries) == 16
    missing = [d for d in deliveries if not d["is_present"]]
    assert len(missing) == 1
    assert missing[0]["source_path"] == "linkedin_ads_2026-06-22.json"
    assert missing[0]["status"] == "fail"


def test_cadence_grid_is_complete(client):
    body = client.get("/api/v1/deliveries/cadence").json()
    assert len(body["cells"]) == len(body["platforms"]) * len(body["weeks"]) == 15
    holes = [c for c in body["cells"] if not c["is_present"]]
    assert [(c["platform"], c["week_start"]) for c in holes] == [
        ("linkedin", "2026-06-22")
    ]


def test_cadence_shows_the_original_not_the_quarantined_resend(client):
    """Two files occupy that slot; the grid must show the counted one."""
    body = client.get("/api/v1/deliveries/cadence").json()
    cell = next(
        c
        for c in body["cells"]
        if c["platform"] == "google" and c["week_start"] == "2026-06-15"
    )
    assert cell["quarantined"] is False
    assert cell["status"] == "pass"


def test_delivery_detail_returns_every_check(client):
    deliveries = client.get("/api/v1/deliveries").json()
    target = next(d for d in deliveries if d["source_path"] == "meta_ads_2026-06-08.csv")
    detail = client.get(f"/api/v1/deliveries/{target['delivery_id']}").json()

    assert len(detail["checks"]) == 16
    # Failures sort first.
    assert detail["checks"][0]["status"] == "fail"
    assert detail["checks"][0]["check_id"] == "file.spend_magnitude"
    assert detail["checks"][0]["details"]["inferred_factor"] == 100


# --- Errors -----------------------------------------------------------------


def test_unknown_delivery_returns_problem_json_404(client):
    response = client.get("/api/v1/deliveries/nosuchthing")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["title"] == "Delivery not found"
    assert body["status"] == 404
    assert body["instance"] == "/api/v1/deliveries/nosuchthing"


def test_inverted_date_range_is_422_not_an_empty_result(client):
    """200 with no rows would let a typo masquerade as 'no data'."""
    response = client.get("/api/v1/metrics?date_from=2026-06-30&date_to=2026-06-01")
    assert response.status_code == 422
    assert response.json()["title"] == "Invalid date range"


def test_unknown_platform_is_rejected_at_the_edge(client):
    response = client.get("/api/v1/metrics?platform=tiktok")
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "platform.0"


# --- Ingestion --------------------------------------------------------------


def test_ingestion_endpoint_is_safe_to_call_twice(client):
    first = client.post("/api/v1/ingestions")
    assert first.status_code == 201
    second = client.post("/api/v1/ingestions")
    assert second.status_code == 201

    assert first.json()["deliveries_processed"] == second.json()["deliveries_processed"]
    assert first.json()["rows_accepted"] == second.json()["rows_accepted"]
    # Unchanged after a second ingestion; nothing accumulated.
    assert client.get("/api/v1/metrics").json()["totals"]["spend_usd"] == TRUSTED_TOTAL_SPEND
