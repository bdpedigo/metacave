## Context

The MaterializationEngine periodically creates frozen PostgreSQL databases—point-in-time snapshots of annotation data with segmentation IDs merged into flat tables. These frozen databases serve the query API, which returns results row-by-row via Arrow IPC. For bulk analytical workloads (e.g., retrieving all synapses for a given root ID), this row-oriented access pattern is inefficient.

A prototype script ([lakefront/table_to_deltalake.py](https://github.com/bdpedigo/lakefront/blob/main/scratch/table_to_deltalake.py)) demonstrates the full pipeline: trigger a CSV dump from the materialization service, download/decompress the ~50 GB file, parse with Polars, partition by root ID columns, write Delta Lakes with z-ordering and bloom filters. This works but involves a costly CSV roundtrip that loses type fidelity and requires a dedicated VM.

The catalog service already supports registering Delta Lake assets (`format: "delta"`) with URI-based storage and JSONB properties for extensible metadata. It has format sniffers for both `delta` and `parquet`.

## Goals / Non-Goals

**Goals:**
- Stream data directly from frozen Postgres to Arrow via ADBC, bypassing CSV entirely
- Produce one or more Delta Lakes per table, with configurable partition columns, z-order columns, and bloom filter columns
- Default partition targets derived from B-tree indexes; overridable via API parameters
- Range-bucket partitioning using approximate percentile boundaries from Postgres for reader-friendly min/max statistics
- Automatically determine partition count from table row count and a target file-size heuristic
- Optimize each Delta Lake with configurable z-ordering and bloom filters
- Support three trigger modes: automated (post-frozen-DB), ad-hoc API, and historical backfill
- Keep peak memory bounded regardless of table size via chunked streaming with a flush threshold

**Future work** (not part of this initial change):
- Register each produced Delta Lake in the catalog service
- Bulk workflow to export all tables in a frozen database version, hooked into the frozen-database workflow

**Non-Goals:**
- Resumable writes (v1 uses delete-and-overwrite for partial failures)
- Replacing or removing the existing CSV dump endpoint
- Reading from the live database (target is frozen DBs only for v1)

## Decisions

### 1. ADBC over CSV export

**Decision**: Use `adbc-driver-postgresql` to stream Arrow RecordBatches directly from Postgres, replacing the `gcloud sql export csv` approach.

**Rationale**: ADBC speaks the Postgres binary wire protocol and returns typed Arrow batches natively. This eliminates the 50 GB+ CSV intermediate, preserves types (no boolean "t"/"f" re-parsing), and avoids the `gcloud sql export` single-operation-at-a-time bottleneck on the Cloud SQL instance.

**Alternative considered**: Keep CSV export and process server-side in a Celery task. Rejected because it still pays the CSV tax and the type-fidelity problems remain.

**Alternative considered**: Use psycopg's `COPY ... TO STDOUT WITH BINARY` + manual Arrow conversion. Rejected because ADBC handles the binary protocol natively and yields Arrow batches directly.

### 2. Configurable Delta Lake output with index-derived defaults

**Decision**: Each Delta Lake export is configured by a list of **output specs**, where each spec defines: `partition_by` (column name or null), `partition_strategy` (e.g., `"range"` or `"hash"`; defaults to `"range"`), `n_partitions` (int or `"auto"`), `zorder_columns` (list), `bloom_filter_columns` (list), and `n_partitions` (int or `"auto"`). When no explicit config is provided, the system derives a reasonable set of defaults from table metadata (indexes, column types, etc.). The exact default derivation strategy is TBD and will be refined through experimentation.

**Rationale**: Different tables and use cases need different strategies—some may benefit from partitioning by root IDs, others by spatial columns, others from a single flat z-ordered Delta Lake. Making this fully configurable via the API endpoint ensures no assumptions are baked in, while the default derivation path provides a zero-config starting point.

**Code paths required**: The implementation must support all combinations: partitioned or flat, any column(s) for z-ordering, any column(s) for bloom filters, and any column for partitioning (including spatial columns).

### 3. Buffered streaming with flush threshold

**Decision**: Stream Arrow batches from ADBC with a configurable `chunk_size` (e.g., 1M rows). Accumulate batches in a buffer. When buffer byte size exceeds a flush threshold (e.g., 2 GB), concatenate into an Arrow Table, compute partition columns for each target, and call `write_deltalake(..., mode="append")` for each Delta Lake. After all batches are processed, run z-order optimization, bloom filter creation, and vacuum on each Delta Lake.

**Rationale**: This bounds peak memory to approximately `flush_threshold * 2` (one buffer being written, one accumulating) regardless of total table size. The flush threshold is tunable per deployment.

**Note on small-file write pattern**: This approach produces many small Parquet files before OPTIMIZE compacts them (e.g., ~25 flushes × 64 partitions = ~1,600 files per Delta Lake for a 50 GB table). An alternative would be buffering to per-partition files on local disk to produce fewer, larger uploads. We chose the append-then-optimize pattern because: (1) GCS write costs are trivial at this scale (~$0.008 for 1,600 Class A operations), and (2) `write_deltalake(..., mode="append")` handles partitioned writes natively, so there is less custom plumbing to design and maintain. The append-then-optimize pattern is also idiomatic Delta Lake (how Spark structured streaming, Databricks Auto Loader, etc. all work). A side benefit we're not currently exploiting is durability—each flush commits data to GCS, so a future resumable-write implementation could pick up where a failed run left off. If OPTIMIZE proves slow at high file counts, the simplest lever is increasing the flush threshold.

### 4. Partition assignment strategy (configurable)

**Decision**: The partition assignment function is configurable per output spec. The implementation must support at least three strategies:

- **Range bucketing** (`"range"`): Compute approximate percentile boundaries via a Postgres pre-query, then assign rows to buckets by binary search. This preserves natural ordering within each bucket, making Parquet min/max statistics tight and contiguous—any reader (DuckDB, Polars, Spark) can prune files on predicates like `WHERE pre_pt_root_id = 42` without knowledge of the bucketing scheme.
  ```sql
  SELECT percentile_disc(generate_series(1, N-1) / N::float)
         WITHIN GROUP (ORDER BY partition_column)
  FROM table_name;
  ```
- **Hash bucketing** (`"hash"`): `hash(value) % N`. Simple and produces even bucket sizes, but scatters values randomly so file-level min/max stats are useless for pruning.
- **No partitioning** (`None` / `partition_by: null`): Write a single flat Delta Lake with no partition column. Relies entirely on z-ordering and bloom filters for query performance. Simplest path—no pre-query, no bucket assignment—and viable for smaller tables or when z-order alone provides sufficient pruning. Without partition directories, the output is a flat set of Parquet files (compacted by OPTIMIZE). A reader must consult every file's min/max statistics in the Delta log to determine which files to read—there's no directory-level pruning shortcut. Z-ordering makes those per-file stats tight so most files can still be skipped, and the stat scan itself is cacheable and cheap relative to reading actual data.

Which strategy is the right default (and whether other strategies are needed) is TBD and will be refined through experimentation. The key design requirement is that the strategy is pluggable per output spec.

**Partition count**: When `n_partitions` is `"auto"`, determined by `max(1, estimated_raw_size / target_size)`, where `target_size` defaults to 256 MB post-compression. `row_count` comes from `MaterializedMetadata`. Can be overridden to an explicit integer in the output spec. Ignored when partition strategy is `None`.

**Trade-off**: Range bucketing requires a pre-scan query (one sequential pass for percentiles) but produces reader-friendly output. Hash bucketing is zero-cost to compute but requires partition-aware readers. No partitioning is the simplest and works well for smaller tables, but may produce very large files for big tables unless combined with aggressive z-ordering.

### 5. Dedicated Celery queue

**Decision**: Delta lake writer tasks run on a separate Celery queue (`deltalake`) with dedicated workers sized for streaming (8+ GB RAM).

**Rationale**: The writer task has a fundamentally different resource profile from existing mat engine tasks (root ID updates, annotation ingestion). Mixing them on the same queue risks OOM on small workers or underutilization of large ones.

### 6. Frozen DB as source, with fallback JOIN logic

**Decision**: Read from the merged flat table in the frozen DB. If the table is not merged (e.g., `merge_tables` was skipped or the frozen DB predates the merge workflow), fall back to joining annotation + segmentation tables using the same JOIN logic as `merge_tables` in `create_frozen_database.py`.

**Rationale**: The frozen DB workflow merges tables via `CREATE TABLE temp AS (SELECT a.*, s.* FROM anno JOIN seg ON id)`, drops the originals, and renames. So normally the merged table is what you get. But some historical frozen DBs or edge cases may have unmerged tables. The fallback ensures the writer works in both cases.

### 7. WKB geometry column handling

**Decision**: Geometry columns arrive as binary WKB bytes from ADBC. Decode them per-batch using vectorized operations (Polars `map_batches` with `shapely.from_wkb`) into coordinate arrays (List[Int32]) before writing to Delta Lake.

**Rationale**: Delta Lake consumers need usable coordinates, not opaque WKB blobs. Batch-level vectorized decoding via shapely is fast enough and matches the prototype's approach.

## Risks / Trade-offs

**[ADBC + Cloud SQL connectivity]** ADBC requires a direct TCP connection to Postgres. Cloud SQL typically uses the Cloud SQL Auth Proxy or private IP. The Celery worker needs network access to the frozen DB instance.
→ *Mitigation*: Workers already connect to Cloud SQL for other tasks. Verify ADBC works through the existing proxy/connection path. Spike this early.

**[ADBC + PostGIS geometry types]** ADBC may not handle PostGIS geometry columns cleanly—it could return raw binary, raise errors, or lose type info.
→ *Mitigation*: Spike with a small table that has geometry columns. If ADBC can't handle them, fall back to `COPY ... TO STDOUT WITH BINARY` for those columns or cast to WKB hex in the SQL query.

**[Write amplification]** N indexed columns = N full copies of the table as Delta Lakes. For a 50 GB table with 2 indexed root_id columns, that's ~100 GB+ of Delta Lakes (likely less post-columnar compression).
→ *Mitigation*: Acceptable trade-off for query performance. Columnar compression typically achieves 5-10x on these tables. Monitor storage costs.

**[Worker memory]** Even with bounded flushing, the worker needs enough RAM for the flush buffer × N simultaneous Delta Lake writes.
→ *Mitigation*: Size the dedicated queue workers appropriately (8-16 GB). The flush threshold is configurable.

**[Partial failure]** If the worker dies mid-write, partially written Delta Lakes remain in GCS. Delta Lake's transaction log makes uncommitted data safe to ignore.
→ *Mitigation*: On restart, detect partial Delta Lakes (row count mismatch with `MaterializedMetadata`) and overwrite.
