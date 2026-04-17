## Why

Materialized annotation tables currently live only in frozen PostgreSQL databases. Users who need bulk analytical access (e.g., "give me proofread synapses to inhibitory cells") must query through the materialization API, which has limits and can get bogged down by very large scans. A workaround exists—a `dump_csv_table` endpoint that exports a full table as a compressed CSV to GCS—but this produces 50 GB+ intermediate files, loses type fidelity (booleans become "t"/"f" strings, geometry becomes hex WKB), and requires a separate VM to convert into a query-friendly format.

We need a first-class path from frozen materialization databases to Delta Lake format on cloud storage. In the future these will be registered in the catalog service. Delta Lake gives columnar storage, predicate pushdown, z-ordering, and bloom filters—dramatically faster for the analytical queries downstream consumers actually run.

## What Changes

- **New Celery task** in MaterializationEngine that streams a merged table from a frozen Postgres DB via ADBC, converts to Arrow, and writes one or more Delta Lakes to GCS—each partitioned by a different indexed column (e.g., `pre_pt_root_id`, `post_pt_root_id`).
- **New API endpoint** on the materialize blueprint (`POST /materialize/run/write_deltalake/...`) to trigger delta lake export ad-hoc or for historical versions.
- **New dependencies** added to MaterializationEngine: `adbc-driver-postgresql`, `deltalake`, `polars`.

**Future work** (not part of this initial change):
- **Hook into the frozen-database workflow** so delta lakes are produced automatically after `merge_tables` + `add_indices` + `check_tables` completes, including bulk export of all tables in a version.
- **Catalog registration**: each produced Delta Lake is registered in the catalog service via `POST /api/v1/assets/register` with format `delta`, source metadata, and partition/optimization details in `properties`.

## Capabilities

### New Capabilities
- `deltalake-export`: Streaming export of materialized tables from frozen Postgres to partitioned, optimized Delta Lake files on cloud storage.

### Modified Capabilities

## Impact

- **MaterializationEngine**: New Celery task module, new API endpoint on the materialize blueprint, new Python dependencies (`adbc-driver-postgresql`, `deltalake`, `polars`).
- **Catalog service**: No code changes in this initial change. Future work will use existing `POST /register` endpoint.
- **Infrastructure**: Delta lake writer tasks may need a dedicated Celery queue with workers sized for streaming (8 GB+ RAM). Output bucket for Delta Lake files needs to be provisioned.
- **Existing CSV dump endpoint**: Not removed. Remains available for backward compatibility but is superseded by this for analytical use cases.
