# Architecture

## System overview

```mermaid
flowchart TB
    subgraph sources["data/deliveries/ — 15 weekly files"]
        META["meta_ads_*.csv<br/>MM/DD/YYYY · USD"]
        GOOG["google_ads_*.csv<br/>ISO · micros + currency"]
        LINK["linkedin_ads_*.json<br/>epoch-ms · EUR string"]
        RATES["exchange_rates.json<br/>EUR → USD = 1.08"]
    end

    subgraph pipeline["Ingestion pipeline (Python)"]
        direction TB
        DISC["<b>1. discover</b><br/>sorted path order<br/>delivery_id = sha256(path)<br/>content_sha256 = sha256(bytes)"]
        PARSE["<b>2. parse + normalize</b><br/>one adapter per platform<br/>bad row → RowRejection, never raises"]
        GRID["<b>3. complete the grid</b><br/>3 platforms × 5 weeks = 15 slots<br/>absent slot → synthetic delivery"]
        CHECK["<b>4. run check registry</b><br/>16 checks · row / file / delivery<br/>same signature, self-registering"]
        GRADE["<b>5. classify</b><br/>pass / warn / fail<br/>+ quarantine on any CRITICAL"]
        PERSIST["<b>6. persist</b><br/>scoped replace, 1 txn per delivery<br/>DELETE WHERE delivery_id → INSERT"]
        DISC --> PARSE --> GRID --> CHECK --> GRADE --> PERSIST
    end

    subgraph storage["SQLite (SQLAlchemy)"]
        DEL[("delivery<br/>status · quarantined · sha256")]
        FACT[("metric_fact<br/>spend_usd_micros · source_line")]
        CR[("check_result<br/>outcome + details JSON")]
        RUN[("ingestion_run<br/>mutable timestamps, isolated")]
    end

    subgraph api["FastAPI — /api/v1"]
        MET["GET /metrics<br/>filters · totals · CTR/CPC<br/>excludes quarantined by default"]
        DAILY["GET /metrics/daily<br/>row level + provenance"]
        DLV["GET /deliveries<br/>GET /deliveries/cadence<br/>GET /deliveries/{id}"]
        ING["POST /ingestions"]
    end

    subgraph ui["React + TypeScript (Vite)"]
        MV["Campaign metrics<br/>sortable · filterable · live totals"]
        HV["Data health<br/>cadence matrix + check drill-down"]
    end

    META & GOOG & LINK --> DISC
    RATES -.->|"injected into adapters"| PARSE

    PERSIST --> DEL & FACT & CR
    PERSIST --> RUN

    DEL & FACT --> MET
    DEL & FACT --> DAILY
    DEL & CR --> DLV
    ING -.->|"invokes"| DISC

    MET --> MV
    DAILY --> MV
    DLV --> HV
    ING --> MV

    classDef crit fill:#d9534f22,stroke:#d9534f,color:#e6e8ec
    classDef store fill:#5b9dff22,stroke:#5b9dff,color:#e6e8ec
    class CHECK,GRADE crit
    class DEL,FACT,CR,RUN store
```

## Why the pipeline has two passes

Phase 2 (complete the grid) and phase 4 (checks) both need to see *every*
delivery before any of them can be judged:

| Check | Needs |
|---|---|
| `file.content_duplicate` | every other file's content hash |
| `file.spend_magnitude` | the platform's median across its other weeks |
| `delivery.present` | the full expected platform × week grid |

A single streaming pass would make a verdict depend on how far through the
directory we happened to be — the same data would grade differently on
different runs. So nothing is written until every file has been parsed.

## Idempotency (NFR-1)

```mermaid
sequenceDiagram
    participant P as Pipeline
    participant DB as SQLite
    Note over P: delivery_id = sha256(relative_path)[:16]
    P->>DB: BEGIN
    P->>DB: DELETE metric_fact  WHERE delivery_id = ?
    P->>DB: DELETE check_result WHERE delivery_id = ?
    P->>DB: UPSERT delivery
    P->>DB: INSERT facts (deduplicated)
    P->>DB: INSERT check results
    P->>DB: COMMIT
    Note over P,DB: Re-run → identical rows. Nothing accumulates.
```

Three decisions make this hold:

1. **`delivery_id` from the path, not the content.** Path identity means "the
   same delivery slot", so a corrected re-send under the same name *replaces*
   rather than double-counts. The content hash is a separate column used only
   by the duplicate check.
2. **Scoped delete-then-insert, not upsert.** An upsert would leave stale rows
   behind if a re-delivery contained *fewer* rows than before.
3. **Sorted discovery order.** Two provided files are byte-identical; the one
   processed first is treated as the original. Filesystem order is not stable
   across machines, so discovery sorts by path and the verdict never flips.

One transaction *per delivery*, not per run: a failure on delivery 12 must not
roll back the eleven that succeeded (NFR-2).

## Error isolation (NFR-2)

Three nested boundaries, each degrading rather than aborting:

```
row     adapter.normalize() RETURNS RowRejection      → row excluded, run continues
file    FileParseError caught in _context_for_file()  → delivery graded, run continues
check   exception caught in run_checks()              → recorded as SKIP, other checks continue
```

A crashing check is recorded as **skip**, never **fail** — a check that did not
examine the data must not be allowed to condemn it.

## Traceability (NFR-3)

Every stored fact carries `delivery_id`, `source_line` and `campaign_raw`, so
any figure on screen resolves to a file, a line, and the exact string the
platform sent. `GET /api/v1/metrics/daily` exposes that chain over HTTP.

```
$48,709.02 total
  └─ 13 contributing deliveries (3 quarantined and excluded)
       └─ metric_fact rows, each with delivery_id + source_line
            └─ delivery.source_path + content_sha256
                 └─ the original file in data/deliveries/, unmodified
```
