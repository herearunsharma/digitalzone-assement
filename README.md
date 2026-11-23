# Campaign Data Hub

Normalizes weekly ad-platform deliveries into one dataset, and grades the
quality of every delivery so the numbers and their trustworthiness sit in one
place.

**Stack:** Python 3.12 · FastAPI · SQLAlchemy · SQLite — React 18 · TypeScript · Vite
**Architecture diagram:** [`docs/architecture.md`](docs/architecture.md)
**Original brief:** [`docs/ASSIGNMENT.md`](docs/ASSIGNMENT.md)

---

## 1. Setup

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node 18+.
No database server, no Docker — storage is a local SQLite file.

### Backend

```bash
cd backend && uv sync
```

Run the ingestion pipeline:

```bash
cd backend && uv run python -m app.cli
```

```
run 09229241-abc0-49be-bf58-85f5743aefba  (0.07s)
  deliveries : 16 (pass 8 / warn 4 / fail 4)
  quarantined: 3
  rows       : 394 accepted, 10 rejected, 8 duplicates dropped
```

Re-run it as many times as you like — ingestion is idempotent (§5).

Start the API on <http://127.0.0.1:8000> (interactive docs at `/docs`):

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend && npm install && npm run dev
```

Open <http://localhost:5173>. Vite proxies `/api` to port 8000, so both servers
need to be running. If the database is empty, press **Run ingestion** in the
header.

### Tests

```bash
cd backend && uv run pytest
```

59 tests. The most load-bearing are `test_idempotency.py` (runs ingestion three
times and compares stored state exactly) and
`test_checks_catch_planted_defects.py` (asserts each defect found in the data is
caught by a named check, **and** that the clean deliveries are not flagged).

---

## 2. What the quality checks found

16 checks ran against 16 delivery slots. **11 defects** surfaced across 8 of the
15 provided files, plus one delivery that never arrived.

### Critical — these deliveries are excluded from reported totals

| File | Check | What is wrong |
|---|---|---|
| `meta_ads_2026-06-08.csv` | `file.spend_magnitude` | **Spend is in cents under a `spend_usd` header.** Week totals $480,070 against a ~$4,700 baseline; every value ends `.00`. Measured ratio **104×**, inferred factor 100×. |
| `google_ads_2026-06-15_resend.csv` | `file.content_duplicate` | **Byte-identical re-send** of `google_ads_2026-06-15.csv` (same SHA-256). Ingesting both would silently double that week. |
| `linkedin_ads_2026-06-22.json` | `delivery.present` | **The delivery never arrived.** Invisible to every row- and file-level check — found only by comparing against the expected 3 × 5 grid. |

These three are the dangerous ones: every individual row in the first two parses
perfectly, so nothing short of a file- or delivery-level check would notice.

### Warnings — usable data, with holes

| File | Check | What is wrong |
|---|---|---|
| `google_ads_2026-06-01.csv` | `file.internal_duplicates` | 8 campaign/day rows repeated inside the file (43 rows where 35 expected). Dropped before storage, first occurrence wins. |
| `meta_ads_2026-06-01.csv` | `row.date_parseable` | `06/31/2026` — June has 30 days. A naive parser would roll this into July. |
| `meta_ads_2026-06-01.csv` | `file.date_format_consistency` | 33 rows `MM/DD/YYYY`, 1 row ISO. Parsed correctly, but flagged — silent tolerance is how an export drifts until a format arrives that we parse *wrongly* rather than not at all. |
| `meta_ads_2026-06-01.csv` | `file.campaign_naming` | `brand awareness q2`, `retargeting - us`, `app install push` in lowercase. |
| `meta_ads_2026-06-15.csv` | `file.campaign_naming` | `"Brand Awareness Q2  "`, `"Lead Gen Webinar  "` — trailing whitespace. |
| `meta_ads_2026-06-15.csv` | `row.schema_complete` | 3 rows with an empty `clicks` cell. |
| `meta_ads_2026-06-22.csv` | `row.numeric_valid` | Spend of `-111.41`. |
| `linkedin_ads_2026-06-08.json` | `row.schema_complete` | 5 records omit the `clicks` key entirely (absent, not null). |

Naming variants are merged on a canonical key, so campaign totals are correct —
but a drifting export is one upstream change away from producing a variant that
canonicalisation does *not* merge, so it is still reported.

### The bottom line

| | Spend | vs truth |
|---|---|---|
| **Trusted total** (quarantined excluded) | **$48,709.02** | — |
| Naive total (everything ingested) | $535,221.71 | **11× inflated** |

Blended CPC moves from **$0.154** to **$1.374** — the exact "dashboard looks
wrong" failure the brief describes. Both numbers are available:
`GET /api/v1/metrics?include_quarantined=true` returns the second.

### The full check set

| Level | Check | Severity |
|---|---|---|
| row | `row.schema_complete` · `row.date_parseable` · `row.date_in_window` · `row.numeric_valid` · `row.currency_known` · `row.metric_coherence` | warning |
| file | `file.parseable` · `file.header_schema` · `file.content_duplicate` · `file.spend_magnitude` | **critical** |
| file | `file.internal_duplicates` · `file.row_count` · `file.campaign_naming` · `file.date_format_consistency` | warning |
| delivery | `delivery.present` | **critical** |
| delivery | `delivery.coverage` | warning |

`row.date_in_window` and `row.metric_coherence` found nothing in this dataset.
They are kept deliberately: the first polices the MM/DD/YYYY assumption (if
Meta's dates were ever day-first, rows would fall outside the declared week and
say so), and the second catches figures that parse but cannot describe reality
(clicks exceeding impressions).

---

## 3. Design decisions and trade-offs

### Money is integer micros, never a float

Spend is stored as `spend_usd_micros: int`. Floats drift under accumulation
(`0.1 + 0.2 != 0.3`), and we are graded on totals being exactly right. Google
already delivers micros natively, so integers also match the domain. Division to
dollars happens exactly once, at the API presentation boundary.

The same reasoning drives `json.load(parse_float=Decimal)` when reading exchange
rates: parsed as a float, `1.08` becomes `1.08000000000000007105…` and every
LinkedIn row inherits the error.

### Quarantine, don't auto-correct

The obvious "fix" for `meta_ads_2026-06-08.csv` is to divide by 100. **I chose
not to.** That is guessing at the source's intent and silently rewriting their
data; if the guess is wrong, a plausible-looking wrong number enters the dataset
with nothing marking it.

Instead the delivery is graded `fail`, marked `quarantined`, and its rows are
**still stored** — just excluded from aggregation by default. That satisfies all
four pressures at once: default totals are correct, the rows remain inspectable,
nothing aborted, and the problem is loud in the health view.

### Health status and quarantine are separate fields

A delivery can be graded `fail` while its surviving rows are still counted.
`linkedin_ads_2026-06-08.json` is the live example: 23.8% of its records were
rejected (above the 10% fail threshold), but its 16 valid rows are perfectly
good and contribute to totals. Quarantine is reserved for deliveries whose
numbers **as a whole** cannot be believed.

This is also why no row-level check is CRITICAL — the bad row has already been
excluded, so the delivery is *incomplete*, not *untrustworthy*. Throwing away a
good week over one empty cell destroys more information than it protects.

### A check registry, not a chain of conditionals

Every check — row, file or delivery level — is a plain function
`DeliveryContext -> CheckOutcome` registered by a decorator:

```python
@check(id="file.my_check", level=FILE, severity=WARNING, description="...")
def my_check(ctx: DeliveryContext) -> CheckOutcome:
    return ok("fine") if ... else fail("...", offending_lines=[...])
```

Adding a check touches one file. The runner, pipeline, schema and API are
untouched. `file.date_format_consistency` was added this way *after* the first
full run, as a working demonstration.

### SQLite

The brief's hard bar is that the submission "must run from a fresh clone by
following your README alone". A single-file database has no daemon, no port and
nothing to fail on the reviewer's machine. The dataset is ~400 rows; Postgres
would buy nothing. SQLAlchemy keeps the migration path to a configuration
change — `CAMPAIGN_HUB_DATABASE_URL=postgresql+psycopg://…` is already honoured.

### No pandas

Every transformation here is a handful of explicit lines over stdlib `csv` and
`json`. A DataFrame would hide the exact coercion rules behind library semantics
— and those rules (what counts as a valid date, when a row is refused) are the
substance of the assessment. 400 rows needs no vectorisation.

### CPC is `null` when clicks is 0

Not `0.0`, not infinity. A campaign that spent money and got no clicks has an
*undefined* cost per click; reporting `0.0` would read as "this traffic was
free" and sort to the top of a cheapest-CPC view. Same for CTR when impressions
is 0. The UI renders both as an em dash.

### Sorted discovery order is load-bearing

The two 06-15 Google files are byte-identical, so whichever is processed first
is treated as the original. Filesystem iteration order is not stable across
machines, so discovery sorts by path — otherwise a re-run could flip which file
is quarantined and idempotency would fail intermittently.

### Synchronous ingestion endpoint

`POST /api/v1/ingestions` runs the pipeline inline and returns `201` with the
completed run, rather than `202` with a job id. The dataset completes in ~70 ms;
a job queue would add moving parts and a polling endpoint to solve a problem
that does not exist yet. Past roughly a few seconds of work, this should become
a background task with a run-status endpoint — the `ingestion_run` table is
already shaped for it.

---

## 4. API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/metrics` | Totals + groups by campaign or platform. Filters: `platform` (repeatable), `date_from`, `date_to`, `group_by`, `include_quarantined` |
| `GET` | `/api/v1/metrics/daily` | Row-level with `delivery_id` + `source_line` (traceability) |
| `GET` | `/api/v1/deliveries` | All deliveries with health status. Filters: `platform`, `status` |
| `GET` | `/api/v1/deliveries/cadence` | Platform × week grid, including slots that never arrived |
| `GET` | `/api/v1/deliveries/{id}` | Full quality report — every check, outcome and evidence |
| `POST` | `/api/v1/ingestions` | Run (or re-run) the pipeline |
| `GET` | `/api/v1/ingestions` | Run history |
| `GET` | `/api/v1/health` | Liveness |

Errors are [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) `problem+json` —
one shape for every status code, so a client writes one error handler:

```json
{ "type": "about:blank", "title": "Invalid date range", "status": 422,
  "detail": "date_from (2026-06-30) is after date_to (2026-06-01).",
  "instance": "/api/v1/metrics",
  "errors": [{"field": "date_from", "message": "must be on or before date_to"}] }
```

An inverted date range is `422`, not `200` with an empty list — a typo must not
masquerade as "no data for that period". An unknown platform is rejected by the
Pydantic enum before handler code runs.

---

## 5. Non-functional requirements

**Idempotency.** `delivery_id` is `sha256(relative_path)[:16]` — derived from
the path, so a corrected re-send replaces rather than duplicates. Each delivery
is written in one transaction as a scoped delete-then-insert. Verified by
`test_idempotency.py`, which ingests three times and asserts `metric_fact`,
`check_result` and `delivery` are identical (excluding `ingested_at`, which
records when we last *looked* at the file; mutable run timestamps live in the
separate `ingestion_run` table precisely so this comparison can be exact).

**Error isolation.** Three boundaries. A bad row *returns* a `RowRejection`
rather than raising — error isolation expressed in the type system, so there is
no exception to swallow in the hot loop. A `FileParseError` is caught per file.
A check that raises is recorded as **skip**, never fail: a check that did not
examine the data must not condemn it.

**Traceability.** Every `metric_fact` carries `delivery_id`, `source_line` and
`campaign_raw` — the untouched source string, deliberately *not* stripped, so
whitespace variants remain detectable and provable.

---

## 6. What I would do with more time

Roughly in the order I would pick them up.

1. **A campaign registry.** `EXPECTED_CAMPAIGN_COUNT` is currently derived from
   the delivered data, which means the row-count and coverage checks would miss
   an entire campaign disappearing from a platform's export. A registry of
   expected campaigns per platform per week closes that hole. **This is the
   biggest remaining gap in check coverage.**
2. **Alerting, not just reporting.** The system now surfaces a bad delivery to
   anyone who opens the health view. The failure in the brief is that nobody
   *looks*. A notification on any `fail`, plus the cadence check running on a
   schedule rather than only at ingestion, is what actually closes the loop.
3. **Incremental ingestion.** Every run re-reads all 15 files. Skipping
   deliveries whose `content_sha256` and path are unchanged would keep the run
   cheap as the archive grows — while still re-running cross-delivery checks,
   whose verdicts depend on siblings.
4. **Per-check configurable thresholds.** `SPEND_MAGNITUDE_RATIO` is a single
   global. A `checks.yaml` with per-platform overrides would let a platform with
   genuinely spiky spend relax it without a code change.
5. **Generated TypeScript types.** `frontend/src/api/types.ts` is hand-written
   and can drift from the Pydantic models. Generating it from the OpenAPI schema
   removes that class of bug; I kept it manual so the project compiles from a
   clean clone without an extra build step.
6. **Server-side sort and pagination.** Sorting is client-side on tens of rows,
   which is right at this size and wrong at thousands.
7. **Filters in the URL.** Filter state is component state, so a filtered view
   cannot be shared or survive a reload.
8. **`docker-compose.yml`.** Deliberately skipped — the brief calls it optional
   and warns against spending FR time on it, and SQLite already makes
   fresh-clone startup trivial.
9. **Property-based tests** over the adapters (Hypothesis) to probe the date and
   currency parsers with inputs I did not think of.

### Known limitations

- The spend-magnitude baseline pools *all* of a platform's other deliveries,
  including quarantined ones. It is robust here because the median ignores
  outliers, but a platform with two unit-error weeks out of five could skew it.
  Excluding already-quarantined siblings from the baseline is a one-line change
  I left out rather than tune against this specific dataset.
- `delivery.coverage` and `file.row_count` overlap: one says *which*
  observations are missing, the other *how many*. Both are reported, so a single
  defect can raise two warnings.
- The expected week grid is hardcoded in `config.py` for June 2026. A real
  system would derive it from the reporting period.
